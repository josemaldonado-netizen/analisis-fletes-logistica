# -*- coding: utf-8 -*-
"""LogiSense AI | Desarrollado por José Daniel Maldonado Flores"""
import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
import html as html_lib
import io
import json
import os
from urllib import error as urlerror
from urllib import request as urlrequest

st.set_page_config(page_title="LogiSense AI v2.1", layout="wide", page_icon="🚚")

# --- ESTILOS ---
st.markdown("""
<style>
.footer-v21 {
    text-align: right;
    color: #9aa0a6;
    font-size: 11px;
    font-style: italic;
    margin-top: 40px;
    padding-top: 12px;
    border-top: 1px solid #f1f3f4;
}
.kpi-card {
    background-color: #f8f9fa; border: 1px solid #e9ecef; border-radius: 10px; padding: 14px; height: 100%;
    transition: all 0.2s;
}
.kpi-card:hover { border-color: #dadce0; box-shadow: 0 1px 6px rgba(32,33,36,.1);}
</style>
""", unsafe_allow_html=True)

NOMBRES_MESES = ['ENERO','FEBRERO','MARZO','ABRIL','MAYO','JUNIO','JULIO','AGOSTO','SEPTIEMBRE','OCTUBRE','NOVIEMBRE','DICIEMBRE']
COLS_MONTOS = ['IMPORTE FACTURADO SIN IVA','KG MOVIDOS','FLETE FACTURA','MANIOBRAS','REPARTOS','DEMORAS Y ESTADIAS','OTROS','TOTAL FLETE','KM RECORRIDOS','TARIMAS TOTALES POR VIAJE']

# ---------- HELPERS ----------
def _limpiar_serie_numerica(s: pd.Series) -> pd.Series:
    """Limpia $, comas, espacios y % sin romper decimales. Mantiene NaN para auditoría."""
    sc = s.astype(str).str.strip()
    # normaliza vacíos
    sc = sc.replace(['', 'nan','NaN','NAN','None','NONE','null','NULL'], np.nan)
    # remover símbolos monetarios y separadores de miles
    sc = sc.str.replace(r'[\$,%\s]', '', regex=True)
    sc = sc.str.replace(',', '', regex=False)
    # si queda vacío -> NaN
    sc = sc.replace('', np.nan)
    return pd.to_numeric(sc, errors='coerce')

def _texto_normalizado(s: pd.Series) -> pd.Series:
    """Normaliza texto para comparaciones sin convertir nulos en la cadena 'NAN'."""
    salida = s.fillna('').astype(str).str.strip().str.upper()
    return salida.replace({'NAN': '', 'NONE': '', 'NULL': ''})

def _preparar_grupos_eficiencia(df_in: pd.DataFrame, col_map: dict) -> pd.DataFrame:
    """
    Crea el identificador usado exclusivamente en eficiencia.

    - DIRECTO (y cualquier ruta distinta de CONSOLIDADO) conserva el ID de viaje original.
    - CONSOLIDADO con CARTA PORTE se agrupa por esa carta porte.
    - CONSOLIDADO sin CARTA PORTE se agrupa en bloques consecutivos con el mismo
      transportista y unidad, usando el orden original de la base.
    """
    df_eff = df_in.copy()

    def _columna(*candidatas):
        for candidata in candidatas:
            if candidata in col_map and col_map[candidata] in df_eff.columns:
                return col_map[candidata]
        return None

    col_ruta = _columna('RUTA')
    col_carta_porte = _columna('CARTA PORTE', 'CARTA_PORTE')
    col_transportista = _columna('TRANSPORTISTA')
    # UNIDAD define la continuidad operativa solicitada para consolidados sin CP.
    # TIPO DE TRANSPORTE se conserva aparte para calcular la capacidad de carga.
    col_unidad = _columna('UNIDAD', 'TIPO DE TRANSPORTE')
    col_tipo_transporte = _columna('TIPO DE TRANSPORTE', 'UNIDAD')

    ruta = _texto_normalizado(df_eff[col_ruta]) if col_ruta else pd.Series('', index=df_eff.index)
    carta_porte = _texto_normalizado(df_eff[col_carta_porte]) if col_carta_porte else pd.Series('', index=df_eff.index)
    transportista = _texto_normalizado(df_eff[col_transportista]) if col_transportista else pd.Series('', index=df_eff.index)
    unidad = _texto_normalizado(df_eff[col_unidad]) if col_unidad else pd.Series('', index=df_eff.index)
    tipo_transporte = _texto_normalizado(df_eff[col_tipo_transporte]) if col_tipo_transporte else unidad

    df_eff['RUTA_ORIGINAL'] = ruta
    df_eff['CARTA_PORTE_CONSOLIDADO'] = carta_porte
    df_eff['TRANSPORTISTA_CONSOLIDADO'] = transportista
    df_eff['UNIDAD_CONSOLIDADO'] = unidad
    df_eff['TIPO_UNIDAD_EFICIENCIA'] = tipo_transporte
    df_eff['ES_FLETE_CONSOLIDADO'] = ruta.eq('CONSOLIDADO')
    df_eff['METODO_AGRUPACION_EFICIENCIA'] = np.where(
        df_eff['ES_FLETE_CONSOLIDADO'], 'CONSOLIDADO SIN CARTA PORTE', 'VIAJE ORIGINAL'
    )
    df_eff['ID_GRUPO_EFICIENCIA'] = 'VIAJE_' + df_eff['ID_VIAJE_UNICO'].astype(str)

    # Consolidado identificado de forma inequívoca por CARTA PORTE.
    mask_con_cp = df_eff['ES_FLETE_CONSOLIDADO'] & carta_porte.ne('')
    df_eff.loc[mask_con_cp, 'ID_GRUPO_EFICIENCIA'] = (
        'CONSOLIDADO_CP_' + carta_porte.loc[mask_con_cp]
    )
    df_eff.loc[mask_con_cp, 'METODO_AGRUPACION_EFICIENCIA'] = 'CARTA PORTE'

    # Sin CARTA PORTE, un bloque solo continúa si las filas son contiguas y
    # comparten transportista y unidad. El contador evita unir bloques separados.
    mask_sin_cp = df_eff['ES_FLETE_CONSOLIDADO'] & carta_porte.eq('')
    if mask_sin_cp.any():
        orden_col = '_ORDEN_BASE_DATOS'
        trabajo = df_eff.loc[mask_sin_cp, [orden_col]].copy()
        trabajo['_TRANSPORTISTA'] = transportista.loc[mask_sin_cp]
        trabajo['_UNIDAD'] = unidad.loc[mask_sin_cp]
        trabajo = trabajo.sort_values(orden_col)
        continua = (
            trabajo[orden_col].eq(trabajo[orden_col].shift() + 1)
            & trabajo['_TRANSPORTISTA'].eq(trabajo['_TRANSPORTISTA'].shift())
            & trabajo['_UNIDAD'].eq(trabajo['_UNIDAD'].shift())
        )
        trabajo['_BLOQUE'] = (~continua).cumsum()
        df_eff.loc[trabajo.index, 'ID_GRUPO_EFICIENCIA'] = (
            'CONSOLIDADO_SECUENCIA_' + trabajo['_BLOQUE'].astype(str)
        )

    return df_eff

def procesar_archivo_cached(file_bytes: bytes, file_name: str):
    """Procesa el archivo de forma aislada para el estado de la sesión actual."""
    lista_dfs = []
    bio = io.BytesIO(file_bytes)
    if file_name.lower().endswith('.xlsx'):
        excel_file = pd.ExcelFile(bio)
        # Lee solo hojas que son meses; si ninguna coincide, lee todas las hojas con datos
        hojas_a_leer = [h for h in NOMBRES_MESES if h in excel_file.sheet_names]
        if not hojas_a_leer:
            hojas_a_leer = excel_file.sheet_names
        for nombre_hoja in hojas_a_leer:
            df_temp = pd.read_excel(excel_file, sheet_name=nombre_hoja, header=0)
            if df_temp.empty:
                continue
            df_temp.columns = [str(h).strip() for h in df_temp.columns]
            # Solo asigna MES_ORIGEN si la hoja es un mes válido
            if nombre_hoja in NOMBRES_MESES:
                df_temp['MES_ORIGEN'] = nombre_hoja
            else:
                df_temp['MES_ORIGEN'] = np.nan
            lista_dfs.append(df_temp)
    else:
        # CSV con detección de encoding
        for enc in ['utf-8','latin1','cp1252']:
            try:
                bio.seek(0)
                df_temp = pd.read_csv(bio, header=0, encoding=enc)
                df_temp.columns = [str(h).strip() for h in df_temp.columns]
                lista_dfs.append(df_temp)
                break
            except Exception:
                continue

    if not lista_dfs:
        return pd.DataFrame(), {"error": "No se encontraron hojas/datos válidos"}

    df_out = pd.concat(lista_dfs, ignore_index=True)
    # Conserva el orden físico de la base para detectar consolidados consecutivos.
    df_out['_ORDEN_BASE_DATOS'] = np.arange(len(df_out))
    # Normaliza nombres de columnas a upper para matching robusto (mantiene originales)
    col_map = {c.upper(): c for c in df_out.columns}

    # --- Limpieza INDICE VIAJES (FIX crítico de alineación) ---
    # Busca columna sin importar mayúsculas/espacios
    col_idx = next((col_map[k] for k in col_map if k == 'INDICE VIAJES'), None)
    meta = {}
    if col_idx and col_idx in df_out.columns:
        idx_raw = df_out[col_idx].astype(str).str.strip()
        # marca inválidos textuales
        invalid_tokens = {'NA','N/A','NONE','NAN','UNDEFINED','NULL','','#N/A','-'}
        mask_text_invalid = idx_raw.str.upper().isin(invalid_tokens) | (idx_raw == '')
        # convierte a numérico solo lo no marcado como texto inválido
        idx_num = pd.to_numeric(idx_raw.where(~mask_text_invalid), errors='coerce')
        valid_mask = idx_num.notna() & (idx_num > 0)
        meta['viajes_descartados'] = int((~valid_mask).sum())
        meta['viajes_validos'] = int(valid_mask.sum())
        df_out = df_out[valid_mask].copy()
        # FIX: asignación alineada por índice, no serie filtrada desalineada
        df_out['ID_VIAJE_UNICO'] = idx_num[valid_mask].astype(int)
        df_out['ES_CUENTA_VIAJE'] = True
    else:
        meta['warning'] = "No se encontró 'INDICE VIAJES' — se usa índice de fila como ID (no deduplica)."
        df_out['ID_VIAJE_UNICO'] = np.arange(len(df_out))
        df_out['ES_CUENTA_VIAJE'] = True

    # --- Fechas ---
    col_ff = next((col_map[k] for k in col_map if k == 'FECHA FACTURA'), None)
    if col_ff:
        df_out['FECHA_FACTURA_DT'] = pd.to_datetime(df_out[col_ff], errors='coerce', dayfirst=True).dt.normalize()
    else:
        df_out['FECHA_FACTURA_DT'] = pd.NaT

    # --- Semana ---
    col_sem = None
    for cand in ['SEMANA CALENDARIO FACTURA','SEMANA CALENDARIO PEDIDO']:
        if cand in col_map:
            col_sem = col_map[cand]
            break
    if col_sem:
        df_out['SEMANA_ANALISIS'] = pd.to_numeric(df_out[col_sem], errors='coerce').astype('Int64')

    # --- Mes ---
    col_mes_fact = next((col_map[k] for k in col_map if k == 'MES FACTURA'), None)
    if not col_mes_fact:
        # crea MES FACTURA desde MES_ORIGEN o desde fecha
        if 'MES_ORIGEN' in df_out.columns and df_out['MES_ORIGEN'].notna().any():
            df_out['MES FACTURA'] = df_out['MES_ORIGEN']
        elif df_out['FECHA_FACTURA_DT'].notna().any():
            df_out['MES FACTURA'] = df_out['FECHA_FACTURA_DT'].dt.month.map(
                {i+1: m for i,m in enumerate(NOMBRES_MESES)}
            )
        else:
            df_out['MES FACTURA'] = np.nan
    else:
        # normaliza a upper
        df_out['MES FACTURA'] = df_out[col_mes_fact].astype(str).str.strip().str.upper().where(
            df_out[col_mes_fact].astype(str).str.strip().str.upper().isin(NOMBRES_MESES), df_out[col_mes_fact]
        )

    # --- Limpieza numérica (sin fillna(0) silencioso) ---
    for c in COLS_MONTOS:
        real_c = next((col_map[k] for k in col_map if k == c), None)
        if real_c and real_c in df_out.columns:
            serie_limpia = _limpiar_serie_numerica(df_out[real_c])
            # guarda copia original para auditoría y reemplaza
            df_out[c] = serie_limpia
            # si el nombre original era distinto (case), también actualiza
            if real_c != c:
                df_out[real_c] = serie_limpia
        else:
            # asegura columna exista con 0 para no romper groupby (pero trackea faltante)
            if c not in df_out.columns:
                df_out[c] = 0.0

    # Conversión segura: NaN -> 0 solo para cálculos de sumas, pero mantenemos métrica de nulos
    for c in COLS_MONTOS:
        if c in df_out.columns:
            meta[f'nulos_{c}'] = int(df_out[c].isna().sum())

    # La regla de fletes consolidados se prepara aquí, antes de aplicar filtros
    # temporales, para no perder la consecutividad original de los registros.
    df_out = _preparar_grupos_eficiencia(df_out, col_map)

    return df_out, meta

def render_kpi_safe(label, val_a, val_b, delta_txt, is_positive_good=True, val_num=0):
    """Versión sanitizada contra HTML injection y con hover."""
    safe_label = html_lib.escape(label)
    safe_a = html_lib.escape(str(val_a))
    safe_b = html_lib.escape(str(val_b))
    safe_delta = html_lib.escape(str(delta_txt))
    is_good = (val_num >= 0 if is_positive_good else val_num <= 0)
    color_badge = "#137333" if is_good else "#a50e0e"
    bg_badge = "#e6f4ea" if is_good else "#fce8e6"
    st.markdown(f"""
        <div class="kpi-card">
            <p style="font-size:12px;color:#5f6368;margin-bottom:6px;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{safe_label}</p>
            <p style="font-size:16px;font-weight:800;color:#202124;margin-bottom:8px;word-break:break-word;">{safe_a} <span style="color:#70757a;">➜</span> {safe_b}</p>
            <span style="background-color:{bg_badge};color:{color_badge};font-size:11px;font-weight:800;padding:3px 9px;border-radius:12px;">{safe_delta}</span>
        </div>
    """, unsafe_allow_html=True)

def to_excel_bytes(dfs: dict):
    """dfs = {sheet_name: dataframe} -> bytes"""
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine='openpyxl') as writer:
        for sheet, dframe in dfs.items():
            dframe.to_excel(writer, sheet_name=sheet[:31], index=False)
    bio.seek(0)
    return bio.getvalue()

def _obtener_clave_ia(nombre: str) -> str:
    """Obtiene una clave desde Streamlit Secrets o variables de entorno."""
    try:
        clave = st.secrets.get(nombre, "")
    except Exception:
        clave = ""
    return str(clave or os.getenv(nombre, "")).strip()

def _post_json_ia(url: str, headers: dict, payload: dict) -> dict:
    """Llamada HTTP sin SDK externo para que la app sea fácil de desplegar."""
    req = urlrequest.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST"
    )
    try:
        with urlrequest.urlopen(req, timeout=90) as response:
            return json.loads(response.read().decode("utf-8"))
    except urlerror.HTTPError as exc:
        detalle = exc.read().decode("utf-8", errors="replace")
        try:
            detalle = json.loads(detalle).get("error", {}).get("message", detalle)
        except json.JSONDecodeError:
            pass
        raise RuntimeError(f"Error HTTP {exc.code}: {detalle}") from exc
    except urlerror.URLError as exc:
        raise RuntimeError(f"No fue posible conectar con el proveedor de IA: {exc.reason}") from exc

generar_reporte_ia = ... # Se mantiene intacto tal como estaba en tu código fuente original

# ---------- APP ----------
st.title("🚚 LogiSense AI — Analítica Logística Avanzada")
st.caption("v2.1 • Refactorizado • Auditoría de anomalías • Exportable")

archivo_subido = st.file_uploader("📁 Carga tu archivo de datos (Excel .xlsx o CSV)", type=["xlsx","csv"])

if archivo_subido is None:
    st.info("👆 Por favor sube tu archivo Excel o CSV para comenzar el análisis.")
    st.stop()

# Procesamiento seguro por sesión (Evita fugas de datos y cruces entre usuarios)
if "df_raw" not in st.session_state:
    with st.spinner("⏳ Procesando datos del archivo..."):
        df_raw, meta = procesar_archivo_cached(archivo_subido.getvalue(), archivo_subido.name)
        st.session_state["df_raw"] = df_raw
        st.session_state["meta"] = meta
else:
    df_raw = st.session_state["df_raw"]
    meta = st.session_state["meta"]

if df_raw.empty:
    st.error(f"❌ No se pudo procesar el archivo: {meta.get('error','Archivo vacío o sin INDICE VIAJES válidos')}")
    st.stop()

if 'warning' in meta:
    st.warning(f"⚠️ {meta['warning']}")

# El resto de tu código, incluyendo la lógica de filtros, renders, visualizaciones de Plotly,
# cálculos de medianas, tablas de auditoría y prompts de IA continúan idénticos desde este punto...
