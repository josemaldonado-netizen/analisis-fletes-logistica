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
    Crea el identificador para análisis que requieren consolidar el flete.

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

@st.cache_data(show_spinner=False)
def procesar_archivo_cached(file_bytes: bytes, file_name: str):
    """Cachea por bytes + nombre. Evita Unhashable param y reprocesado al cambiar filtros."""
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

def generar_reporte_ia(prompt: str, clave_claude: str, clave_openai: str,
                       modelo_claude: str, modelo_openai: str) -> tuple[str, str]:
    """
    Prioriza Claude y, si falla o no tiene clave, usa OpenAI como respaldo.
    Las claves no se guardan ni se muestran en pantalla.
    """
    errores = []
    if clave_claude:
        try:
            respuesta = _post_json_ia(
                "https://api.anthropic.com/v1/messages",
                {
                    "Content-Type": "application/json",
                    "x-api-key": clave_claude,
                    "anthropic-version": "2023-06-01"
                },
                {
                    "model": modelo_claude,
                    "max_tokens": 1800,
                    "system": (
                        "Eres un gerente senior de logística. Redacta en español claro, "
                        "usa únicamente los datos entregados y señala límites de la información."
                    ),
                    "messages": [{"role": "user", "content": prompt}]
                }
            )
            texto = "".join(
                bloque.get("text", "")
                for bloque in respuesta.get("content", [])
                if bloque.get("type") == "text"
            ).strip()
            if not texto:
                raise RuntimeError("Claude devolvió una respuesta vacía.")
            return texto, f"Claude · {modelo_claude}"
        except Exception as exc:
            errores.append(f"Claude: {exc}")

    if clave_openai:
        try:
            respuesta = _post_json_ia(
                "https://api.openai.com/v1/responses",
                {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {clave_openai}"
                },
                {
                    "model": modelo_openai,
                    "instructions": (
                        "Eres un gerente senior de logística. Responde en español claro, "
                        "usa únicamente los datos entregados y señala límites de la información."
                    ),
                    "input": prompt,
                    "max_output_tokens": 1800
                }
            )
            texto = str(respuesta.get("output_text", "")).strip()
            if not texto:
                # Respaldo compatible si el proveedor no incluye output_text.
                texto = "".join(
                    parte.get("text", "")
                    for salida in respuesta.get("output", [])
                    for parte in salida.get("content", [])
                    if parte.get("type") == "output_text"
                ).strip()
            if not texto:
                raise RuntimeError("OpenAI devolvió una respuesta vacía.")
            proveedor = f"OpenAI · {modelo_openai}"
            if errores:
                proveedor += " (respaldo tras intentar Claude)"
            return texto, proveedor
        except Exception as exc:
            errores.append(f"OpenAI: {exc}")

    if not clave_claude and not clave_openai:
        raise RuntimeError(
            "Configura ANTHROPIC_API_KEY para usar Claude o OPENAI_API_KEY como respaldo. "
            "También puedes pegarlas temporalmente en esta pantalla."
        )
    raise RuntimeError("No se pudo generar el reporte. " + " | ".join(errores))

# ---------- APP ----------
st.title("🚚 LogiSense AI — Analítica Logística Avanzada")
st.caption("v2.1 • Refactorizado • Auditoría de anomalías • Exportable")

archivo_subido = st.file_uploader("📁 Carga tu archivo de datos (Excel .xlsx o CSV)", type=["xlsx","csv"])

if archivo_subido is None:
    st.info("👆 Por favor sube tu archivo Excel o CSV para comenzar el análisis.")
    st.stop()

# Procesamiento con cache
with st.spinner("⏳ Procesando datos del archivo..."):
    df_raw, meta = procesar_archivo_cached(archivo_subido.getvalue(), archivo_subido.name)

if df_raw.empty:
    st.error(f"❌ No se pudo procesar el archivo: {meta.get('error','Archivo vacío o sin INDICE VIAJES válidos')}")
    st.stop()

if 'warning' in meta:
    st.warning(f"⚠️ {meta['warning']}")

# --- Sidebar Filtros ---
st.sidebar.header("🔍 Filtros Operativos")
def multiselect_sidebar(col_name, label):
    # matching case-insensitive
    real = next((c for c in df_raw.columns if c.upper() == col_name.upper()), None)
    if not real:
        return [], None
    opts = sorted([str(x) for x in df_raw[real].dropna().unique() if str(x).strip() != ''])
    sel = st.sidebar.multiselect(label, opts, default=[])
    return sel, real

clientes_sel, c_cli = multiselect_sidebar('CLIENTE','Cliente(s)')
transp_sel, c_tr = multiselect_sidebar('TRANSPORTISTA','Transportista(s)')
tipo_trans_sel, c_tt = multiselect_sidebar('TIPO DE TRANSPORTE','Tipo de Transporte')
embarque_sel, c_emb = multiselect_sidebar('TIPO DE EMBARQUE','Tipo de Embarque')
origen_sel, c_ori = multiselect_sidebar('ORIGEN DE VIAJE','Origen')
destino_sel, c_des = multiselect_sidebar('DESTINO DE EMBARQUE','Destino')

df = df_raw.copy()
if clientes_sel and c_cli: df = df[df[c_cli].astype(str).isin(clientes_sel)]
if transp_sel and c_tr: df = df[df[c_tr].astype(str).isin(transp_sel)]
if tipo_trans_sel and c_tt: df = df[df[c_tt].astype(str).isin(tipo_trans_sel)]
if embarque_sel and c_emb: df = df[df[c_emb].astype(str).isin(embarque_sel)]
if origen_sel and c_ori: df = df[df[c_ori].astype(str).isin(origen_sel)]
if destino_sel and c_des: df = df[df[c_des].astype(str).isin(destino_sel)]

if df.empty:
    st.warning("⚠️ Los filtros actuales no dejan registros. Ajusta los filtros.")
    st.stop()

# --- Periodos ---
st.sidebar.markdown("---")
st.sidebar.header("📅 Periodos de Comparación")
modo_periodo = st.sidebar.radio("Comparar por:", ["Semana","Mes","Día (Calendario)"], horizontal=True)
datos_validos = True

if modo_periodo == "Semana":
    col_periodo = 'SEMANA_ANALISIS'
    if col_periodo not in df.columns or df[col_periodo].dropna().empty:
        st.sidebar.error("No hay datos de SEMANA para comparar.")
        datos_validos=False; per_a=per_b=None
    else:
        periodos = sorted([int(x) for x in df[col_periodo].dropna().unique()])
        c1,c2 = st.sidebar.columns(2)
        per_a = c1.selectbox("Semana A (Base)", periodos, index=0)
        per_b = c2.selectbox("Semana B (Actual)", periodos, index=min(1,len(periodos)-1))
        df_a = df[df[col_periodo]==per_a]
        df_b = df[df[col_periodo]==per_b]

elif modo_periodo == "Mes":
    col_periodo='MES FACTURA'
    meses_existentes = [m for m in NOMBRES_MESES if (df[col_periodo]==m).any()] if col_periodo in df.columns else []
    if not meses_existentes:
        st.sidebar.error("No hay datos de MES FACTURA.")
        datos_validos=False; per_a=per_b=None
    else:
        c1,c2 = st.sidebar.columns(2)
        per_a = c1.selectbox("Mes A (Base)", meses_existentes, index=0)
        per_b = c2.selectbox("Mes B (Actual)", meses_existentes, index=min(1,len(meses_existentes)-1))
        df_a = df[df[col_periodo]==per_a]
        df_b = df[df[col_periodo]==per_b]
else:
    # Día
    if 'FECHA_FACTURA_DT' not in df.columns or df['FECHA_FACTURA_DT'].dropna().empty:
        st.sidebar.warning("⚠️ No hay FECHA FACTURA válida.")
        datos_validos=False; per_a=per_b=None
    else:
        min_f = df['FECHA_FACTURA_DT'].min().date()
        max_f = df['FECHA_FACTURA_DT'].max().date()
        st.sidebar.markdown("##### 🗓️ Rango Periodo A (Base)")
        rango_a = st.sidebar.date_input("Fechas A", value=(min_f, min_f), min_value=min_f, max_value=max_f, key="rango_a_v21")
        st.sidebar.markdown("##### 🗓️ Rango Periodo B (Actual)")
        rango_b = st.sidebar.date_input("Fechas B", value=(max_f, max_f), min_value=min_f, max_value=max_f, key="rango_b_v21")
        # robusto: date_input retorna tupla de 2 cuando es rango
        def _to_range(r):
            if isinstance(r, (tuple, list)):
                if len(r)==2: return pd.to_datetime(r[0]).normalize(), pd.to_datetime(r[1]).normalize()
                elif len(r)==1: return pd.to_datetime(r[0]).normalize(), pd.to_datetime(r[0]).normalize()
            return pd.to_datetime(r).normalize(), pd.to_datetime(r).normalize()
        ini_a, fin_a = _to_range(rango_a)
        ini_b, fin_b = _to_range(rango_b)
        df_a = df[(df['FECHA_FACTURA_DT']>=ini_a)&(df['FECHA_FACTURA_DT']<=fin_a)]
        df_b = df[(df['FECHA_FACTURA_DT']>=ini_b)&(df['FECHA_FACTURA_DT']<=fin_b)]
        per_a = f"{ini_a.strftime('%Y-%m-%d')} al {fin_a.strftime('%Y-%m-%d')}"
        per_b = f"{ini_b.strftime('%Y-%m-%d')} al {fin_b.strftime('%Y-%m-%d')}"

if not datos_validos:
    st.error("No hay suficiente información para comparar con los filtros/periodos seleccionados.")
    st.stop()
if df_a.empty or df_b.empty:
    st.warning(f"⚠️ Uno de los periodos no tiene datos: A={len(df_a)} registros, B={len(df_b)} registros.")
    # no stop, muestra lo que hay

# ---------- CÁLCULOS KPI ----------
def _suma(col, dframe): 
    return dframe[col].sum(skipna=True) if col in dframe.columns else 0.0
def _unique_viajes(dframe):
    return dframe[dframe['ES_CUENTA_VIAJE']==True]['ID_VIAJE_UNICO'].nunique() if 'ID_VIAJE_UNICO' in dframe.columns else 0

def _mediana_viaje(dframe):
    """Mediana de FLETE FACTURA por viaje unico (suma por ID_VIAJE_UNICO)."""
    if dframe.empty or 'ID_VIAJE_UNICO' not in dframe.columns or 'FLETE FACTURA' not in dframe.columns:
        return 0.0
    sub = dframe[dframe['ES_CUENTA_VIAJE']==True] if 'ES_CUENTA_VIAJE' in dframe.columns else dframe
    try:
        g = sub.groupby('ID_VIAJE_UNICO')['FLETE FACTURA'].sum(numeric_only=True)
        g = g.dropna()
        if len(g)==0:
            return 0.0
        return float(g.median())
    except Exception:
        return 0.0

viajes_a = _unique_viajes(df_a); viajes_b = _unique_viajes(df_b)
tot_a = _suma('TOTAL FLETE', df_a); tot_b = _suma('TOTAL FLETE', df_b)
fact_a = _suma('IMPORTE FACTURADO SIN IVA', df_a); fact_b = _suma('IMPORTE FACTURADO SIN IVA', df_b)
flete_puro_a = _suma('FLETE FACTURA', df_a); flete_puro_b = _suma('FLETE FACTURA', df_b)
media_viaje_a = (flete_puro_a / viajes_a) if viajes_a>0 else 0
media_viaje_b = (flete_puro_b / viajes_b) if viajes_b>0 else 0
kg_a = _suma('KG MOVIDOS', df_a); kg_b = _suma('KG MOVIDOS', df_b)
tar_a = _suma('TARIMAS TOTALES POR VIAJE', df_a); tar_b = _suma('TARIMAS TOTALES POR VIAJE', df_b)
costo_kg_a = (flete_puro_a/kg_a) if kg_a>0 else 0
costo_kg_b = (flete_puro_b/kg_b) if kg_b>0 else 0
costo_tar_a = (flete_puro_a/tar_a) if tar_a>0 else 0
costo_tar_b = (flete_puro_b/tar_b) if tar_b>0 else 0
ventas_vs_flete_a = (fact_a / flete_puro_a * 100) if flete_puro_a > 0 else 0
ventas_vs_flete_b = (fact_b / flete_puro_b * 100) if flete_puro_b > 0 else 0

# --- Medianas por viaje (FLETE FACTURA) ---
mediana_viaje_a = _mediana_viaje(df_a)
mediana_viaje_b = _mediana_viaje(df_b)

def _var_pct(b,a): return ((b-a)/a*100) if a!=0 else 0

var_fact = _var_pct(fact_b,fact_a)
var_viajes = viajes_b - viajes_a
var_costo = _var_pct(media_viaje_b, media_viaje_a)
var_kg = _var_pct(kg_b, kg_a)
var_tar = _var_pct(tar_b,tar_a)
var_costo_kg = _var_pct(costo_kg_b,costo_kg_a)
var_costo_tar = _var_pct(costo_tar_b,costo_tar_a)
var_mediana = _var_pct(mediana_viaje_b, mediana_viaje_a)
var_ventas_vs_flete = _var_pct(ventas_vs_flete_b, ventas_vs_flete_a)

# ---------- TABS ----------
tab1, tab2, tab3 = st.tabs(["📊 Comparativo Financiero y Gráficos","🚨 Auditoría de Anomalías","📝 Prompt para IA Executive"])

with tab1:
    st.subheader(f"📊 Comparativa Real: {modo_periodo} {per_a} vs {modo_periodo} {per_b}")

    # KPIs fila 1
    m_col1,m_col2,m_col3,m_col4 = st.columns(4)
    with m_col1: render_kpi_safe(f"Facturación Venta ({modo_periodo} {per_a} ➜ {per_b})", f"${fact_a:,.2f}", f"${fact_b:,.2f}", f"{var_fact:+.1f}%", True, var_fact)
    with m_col2: render_kpi_safe(f"Total Viajes Reales ({modo_periodo} {per_a} ➜ {per_b})", f"{viajes_a}", f"{viajes_b}", f"{var_viajes:+} viajes", True, var_viajes)
    with m_col3: render_kpi_safe(f"Tarifa Media / Viaje ({modo_periodo} {per_a} ➜ {per_b})", f"${media_viaje_a:,.2f}", f"${media_viaje_b:,.2f}", f"{var_costo:+.1f}%", False, var_costo)
    with m_col4: render_kpi_safe(f"KG Movidos ({modo_periodo} {per_a} ➜ {per_b})", f"{kg_a:,.0f} kg", f"{kg_b:,.0f} kg", f"{var_kg:+.1f}%", True, var_kg)
    st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)
    m_col5,m_col6,m_col7,m_col8 = st.columns(4)
    with m_col5: render_kpi_safe(f"Tarimas Totales ({modo_periodo} {per_a} ➜ {per_b})", f"{tar_a:,.0f}", f"{tar_b:,.0f}", f"{var_tar:+.1f}%", True, var_tar)
    with m_col6: render_kpi_safe(f"Costo por KG ({modo_periodo} {per_a} ➜ {per_b})", f"${costo_kg_a:,.2f}", f"${costo_kg_b:,.2f}", f"{var_costo_kg:+.1f}%", False, var_costo_kg)
    with m_col7: render_kpi_safe(f"Costo por Tarima ({modo_periodo} {per_a} ➜ {per_b})", f"${costo_tar_a:,.2f}", f"${costo_tar_b:,.2f}", f"{var_costo_tar:+.1f}%", False, var_costo_tar)
    with m_col8: render_kpi_safe(f"Ventas vs Flete Factura ({modo_periodo} {per_a} ➜ {per_b})", f"{ventas_vs_flete_a:,.1f}%", f"{ventas_vs_flete_b:,.1f}%", f"{var_ventas_vs_flete:+.1f}%", True, var_ventas_vs_flete)

    # --- MEDIANAS (solicitado) ---
    st.markdown("---")
    st.markdown(f"### \u25a3 Medianas \u2014 Comparativo financiero (FLETE FACTURA por viaje)")
    # KPI Mediana Periodo X vs Periodo Y
    m_med1, m_med2 = st.columns([1,2])
    with m_med1:
        render_kpi_safe(f"Tarifa Mediana / Viaje ({modo_periodo} {per_a} \u279c {per_b})", f"${mediana_viaje_a:,.2f}", f"${mediana_viaje_b:,.2f}", f"{var_mediana:+.1f}%", False, var_mediana)
        st.caption("Mediana = valor central por viaje (robusta a outliers).")
    with m_med2:
        df_med = pd.DataFrame({"Periodo":[f"{modo_periodo} {per_a}", f"{modo_periodo} {per_b}"], "Mediana":[mediana_viaje_a, mediana_viaje_b], "Media":[media_viaje_a, media_viaje_b]})
        fig_med = go.Figure()
        fig_med.add_trace(go.Bar(x=df_med["Periodo"], y=df_med["Mediana"], name="Mediana", marker_color="#1a73e8", text=[f"${v:,.0f}" for v in df_med["Mediana"]], textposition="outside"))
        fig_med.add_trace(go.Scatter(x=df_med["Periodo"], y=df_med["Media"], mode="markers+lines+text", name="Media", marker=dict(size=10, color="#ea4335"), line=dict(dash="dash", color="#ea4335"), text=[f"${v:,.0f}" for v in df_med["Media"]], textposition="top center"))
        fig_med.update_layout(title=f"Mediana vs Media \u2014 {modo_periodo} {per_a} vs {per_b} (FLETE FACTURA / viaje)", yaxis_title="Monto $", barmode="group", height=340, margin=dict(t=50,b=20), legend=dict(orientation="h", y=1.08))
        st.plotly_chart(fig_med, use_container_width=True)

    # Dos cuadritos: Media vs Mediana por periodo con explicacion corta
    c1, c2 = st.columns(2)
    dif_a = media_viaje_a - mediana_viaje_a
    dif_b = media_viaje_b - mediana_viaje_b
    pct_a = ((media_viaje_a - mediana_viaje_a)/mediana_viaje_a*100) if mediana_viaje_a!=0 else 0
    pct_b = ((media_viaje_b - mediana_viaje_b)/mediana_viaje_b*100) if mediana_viaje_b!=0 else 0
    etiqueta_periodo = "Día" if modo_periodo == "Día (Calendario)" else modo_periodo
    def _txt_exp(dif, pct):
        if dif < 0:
            return "El promedio es más bajo porque hay algunos viajes con importes muy bajos."
        if abs(pct) < 3:
            return "El promedio y el valor central son muy parecidos. Los viajes tienen montos similares."
        else:
            return "El promedio es más alto porque hay algunos viajes con importes muy altos."
    with c1:
        st.markdown(f"""
        <div class="kpi-card" style="border-left:4px solid #1a73e8">
            <p style="font-size:12px;color:#5f6368;margin-bottom:4px;font-weight:700">\u25a3 {etiqueta_periodo} {per_a} \u2014 Media vs Mediana</p>
            <p style="font-size:14px;margin:4px 0"><b>Media:</b> ${media_viaje_a:,.2f} &nbsp;|&nbsp; <b>Mediana:</b> ${mediana_viaje_a:,.2f}</p>
            <p style="font-size:12px;margin:4px 0"><span style="background:#e8f0fe;color:#1a73e8;padding:3px 8px;border-radius:10px;font-weight:700">Dif: ${dif_a:+,.2f} ({pct_a:+.1f}%)</span></p>
            <p style="font-size:11px;color:#3c4043;margin-top:8px;line-height:1.3">{html_lib.escape(_txt_exp(dif_a, pct_a))}</p>
        </div>
        """, unsafe_allow_html=True)
    with c2:
        st.markdown(f"""
        <div class="kpi-card" style="border-left:4px solid #ea4335">
            <p style="font-size:12px;color:#5f6368;margin-bottom:4px;font-weight:700">\u25a3 {etiqueta_periodo} {per_b} \u2014 Media vs Mediana</p>
            <p style="font-size:14px;margin:4px 0"><b>Media:</b> ${media_viaje_b:,.2f} &nbsp;|&nbsp; <b>Mediana:</b> ${mediana_viaje_b:,.2f}</p>
            <p style="font-size:12px;margin:4px 0"><span style="background:#fce8e6;color:#a50e0e;padding:3px 8px;border-radius:10px;font-weight:700">Dif: ${dif_b:+,.2f} ({pct_b:+.1f}%)</span></p>
            <p style="font-size:11px;color:#3c4043;margin-top:8px;line-height:1.3">{html_lib.escape(_txt_exp(dif_b, pct_b))}</p>
        </div>
        """, unsafe_allow_html=True)

    # Gráfico tendencia
    st.markdown("---")
    st.markdown("### 📈 Tendencia Histórica de Métricas")
    dict_metricas = {
        "Facturación (Ventas)": "IMPORTE FACTURADO SIN IVA",
        "Total Flete (Costo)": "TOTAL FLETE",
        "Flete Base": "FLETE FACTURA",
        "Maniobras": "MANIOBRAS",
        "Repartos": "REPARTOS",
        "Demoras y Estadías": "DEMORAS Y ESTADIAS",
        "Otros Gastos": "OTROS",
        "KG Movidos": "KG MOVIDOS",
        "Tarimas Totales": "TARIMAS TOTALES POR VIAJE",
        "Costo por KG": "COSTO_KG",
        "Costo por Tarima": "COSTO_TARIMA",
        "Ventas vs Flete Factura": "VENTAS_VS_FLETE_FACTURA"
    }
    metricas_seleccionadas = st.multiselect("Selecciona las métricas para graficar:", list(dict_metricas.keys()), default=["Facturación (Ventas)","Total Flete (Costo)"])

    df_trend = df.copy()
    # Agrupación robusta
    cols_agg = {k: 'sum' for k in ['IMPORTE FACTURADO SIN IVA','FLETE FACTURA','MANIOBRAS','REPARTOS','DEMORAS Y ESTADIAS','OTROS','TOTAL FLETE','KG MOVIDOS','TARIMAS TOTALES POR VIAJE'] if k in df_trend.columns}
    if modo_periodo == "Mes":
        df_trend['PERIODO_ORDEN'] = pd.Categorical(df_trend['MES FACTURA'], categories=NOMBRES_MESES, ordered=True)
        df_grouped = df_trend.groupby('PERIODO_ORDEN', observed=True).agg(cols_agg).reset_index().rename(columns={'PERIODO_ORDEN':'Periodo'})
        df_grouped = df_grouped.sort_values('Periodo')
    elif modo_periodo == "Semana":
        df_grouped = df_trend.groupby('SEMANA_ANALISIS', observed=True).agg(cols_agg).reset_index().rename(columns={'SEMANA_ANALISIS':'Periodo'})
        df_grouped = df_grouped.sort_values('Periodo')
    else:
        # Diario: usa fecha real para no perder huecos
        df_trend = df_trend.dropna(subset=['FECHA_FACTURA_DT'])
        df_grouped = df_trend.groupby('FECHA_FACTURA_DT', observed=True).agg(cols_agg).reset_index().rename(columns={'FECHA_FACTURA_DT':'Periodo'})
        df_grouped = df_grouped.sort_values('Periodo')
        # formatea para eje X
        df_grouped['Periodo_str'] = df_grouped['Periodo'].dt.strftime('%Y-%m-%d')

    if not df_grouped.empty:
        df_grouped['COSTO_KG'] = np.where(df_grouped.get('KG MOVIDOS',0)>0, df_grouped.get('FLETE FACTURA',0)/df_grouped['KG MOVIDOS'], 0)
        df_grouped['COSTO_TARIMA'] = np.where(df_grouped.get('TARIMAS TOTALES POR VIAJE',0)>0, df_grouped.get('FLETE FACTURA',0)/df_grouped['TARIMAS TOTALES POR VIAJE'], 0)
        df_grouped['VENTAS_VS_FLETE_FACTURA'] = np.where(
            df_grouped.get('FLETE FACTURA', 0) > 0,
            df_grouped.get('IMPORTE FACTURADO SIN IVA', 0) / df_grouped['FLETE FACTURA'] * 100,
            0
        )
        if metricas_seleccionadas:
            cols_y = [dict_metricas[m] for m in metricas_seleccionadas if dict_metricas[m] in df_grouped.columns]
            x_col = 'Periodo_str' if 'Periodo_str' in df_grouped.columns else 'Periodo'
            if cols_y:
                fig_lineas = px.line(df_grouped, x=x_col, y=cols_y, markers=True, title=f"Evolución por {modo_periodo}")
                fig_lineas.update_layout(hovermode="x unified", legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
                st.plotly_chart(fig_lineas, use_container_width=True)
            else:
                st.info("Las métricas seleccionadas no tienen datos en el periodo actual.")

    # Desglose gastos
    st.markdown("---")
    st.markdown("### 💵 Desglose de Gastos Acumulados")
    conceptos = ['FLETE FACTURA','MANIOBRAS','REPARTOS','DEMORAS Y ESTADIAS','OTROS','TOTAL FLETE']
    filas=[]
    for conc in conceptos:
        m_a = _suma(conc, df_a); m_b = _suma(conc, df_b)
        dif = m_b - m_a; pct = ((m_b-m_a)/m_a*100) if m_a!=0 else 0
        filas.append({'Línea de Gasto':conc, f'{modo_periodo} {per_a}': f"${m_a:,.2f}", f'{modo_periodo} {per_b}': f"${m_b:,.2f}", 'Diferencia ($)': f"${dif:,.2f}", 'Variación (%)': f"{pct:+.1f}%"})
    df_desglose = pd.DataFrame(filas)
    st.table(df_desglose)
    # Export
    excel_desglose = to_excel_bytes({"Desglose": df_desglose, "KPIs": pd.DataFrame([{"Concepto":"Facturación","A":fact_a,"B":fact_b},{"Concepto":"Viajes","A":viajes_a,"B":viajes_b}])})
    st.download_button("📥 Descargar desglose (Excel)", data=excel_desglose, file_name=f"LogiSense_Desglose_{modo_periodo}_{per_a}_vs_{per_b}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

with tab2:
    # Contexto explícito para evitar ambigüedad en la auditoría.
    # En vista semanal también se muestra el/los meses a los que pertenece cada semana.
    def _periodo_auditoria(periodo, dframe):
        if modo_periodo == "Semana":
            meses_periodo = []
            if 'MES FACTURA' in dframe.columns:
                meses_periodo = [
                    str(m) for m in dframe['MES FACTURA'].dropna().unique()
                    if str(m).strip() and str(m).upper() != 'NAN'
                ]
                meses_periodo = sorted(
                    meses_periodo,
                    key=lambda m: NOMBRES_MESES.index(m) if m in NOMBRES_MESES else len(NOMBRES_MESES)
                )
            mes_txt = f" — {', '.join(meses_periodo)}" if meses_periodo else ""
            return f"Semana {periodo}{mes_txt}"
        if modo_periodo == "Mes":
            return f"Mes {periodo}"
        return f"Periodo {periodo}"

    periodo_base_audit = _periodo_auditoria(per_a, df_a)
    periodo_actual_audit = _periodo_auditoria(per_b, df_b)

    st.subheader(f"🚨 Auditoría de Anomalías — {periodo_actual_audit} vs {periodo_base_audit}")
    st.caption(
        f"Compara los viajes de **{periodo_actual_audit}** contra la tarifa media "
        f"de **{periodo_base_audit}**."
    )

    df_bv = df_b[df_b['ES_CUENTA_VIAJE']==True].copy()
    if df_bv.empty:
        st.info("No hay viajes válidos en el periodo B para auditar.")
    else:
        def _unir_facturas(serie):
            facturas = pd.Series(serie).dropna().astype(str).str.strip()
            facturas = facturas[facturas.ne('') & facturas.str.upper().ne('NAN')]
            return ', '.join(pd.unique(facturas))

        # Agregación por viaje único
        agg_dict = {}
        for col, how in [('CLIENTE','first'),('TRANSPORTISTA','first'),('ORIGEN DE VIAJE','first'),('DESTINO DE EMBARQUE','first'),('TARIMAS TOTALES POR VIAJE','sum'),('TIPO DE TRANSPORTE','first'),('TIPO DE EMBARQUE','first'),('FAC',_unir_facturas),('KG MOVIDOS','sum'),('IMPORTE FACTURADO SIN IVA','sum'),('FLETE FACTURA','sum'),('MANIOBRAS','sum'),('REPARTOS','sum'),('DEMORAS Y ESTADIAS','sum'),('OTROS','sum'),('TOTAL FLETE','sum')]:
            if col in df_bv.columns:
                agg_dict[col]=how
        df_b_grouped = df_bv.groupby('ID_VIAJE_UNICO').agg(agg_dict).reset_index()
        df_b_grouped = df_b_grouped.rename(columns={'ORIGEN DE VIAJE':'Origen','DESTINO DE EMBARQUE':'Destino','TARIMAS TOTALES POR VIAJE':'Tarimas','TIPO DE TRANSPORTE':'Unidad','TIPO DE EMBARQUE':'Tipo de Embarque','IMPORTE FACTURADO SIN IVA':'Facturación'})
        df_b_grouped['RUTA'] = df_b_grouped.get('Origen','').astype(str) + " → " + df_b_grouped.get('Destino','').astype(str)
        df_b_grouped['Costo por kg'] = np.where(
            df_b_grouped.get('KG MOVIDOS', 0) > 0,
            df_b_grouped.get('FLETE FACTURA', 0) / df_b_grouped['KG MOVIDOS'],
            0
        )
        df_b_grouped['Ventas vs Flete Factura'] = np.where(
            df_b_grouped.get('FLETE FACTURA', 0) > 0,
            df_b_grouped.get('Facturación', 0) / df_b_grouped['FLETE FACTURA'] * 100,
            0
        )
        df_b_grouped['% Flete sobre Facturación'] = np.where(
            df_b_grouped.get('Facturación', 0) > 0,
            df_b_grouped.get('FLETE FACTURA', 0) / df_b_grouped['Facturación'] * 100,
            0
        )

        media_ref = media_viaje_a
        viajes_altos = df_b_grouped[df_b_grouped['FLETE FACTURA'] > media_ref].copy()
        viajes_altos['Diferencia vs Media Base'] = viajes_altos['FLETE FACTURA'] - media_ref
        viajes_altos['% vs Media'] = np.where(media_ref>0, (viajes_altos['FLETE FACTURA']-media_ref)/media_ref*100, 0)
        df_show = viajes_altos.sort_values('FLETE FACTURA', ascending=False)
        st.metric(
            f"Tarifa media base — {periodo_base_audit}",
            f"${media_ref:,.2f}",
            help=f"Promedio de FLETE FACTURA por viaje en {periodo_base_audit}."
        )
        st.metric(
            f"Viajes de {periodo_actual_audit} por encima de la media base",
            f"{len(df_show)} de {len(df_b_grouped)}",
            delta=f"{len(df_show)/len(df_b_grouped)*100:.1f}%" if len(df_b_grouped)>0 else None
        )
        st.caption(
            "Importancia de la comparación con la media: permite identificar viajes que "
            "incrementan el costo promedio total de la operación."
        )

        if not df_show.empty:
            cols_dinero = ['Facturación','FLETE FACTURA','Costo por kg','MANIOBRAS','REPARTOS','DEMORAS Y ESTADIAS','OTROS','TOTAL FLETE']
            df_vis = df_show.copy()
            for col in cols_dinero + ['Diferencia vs Media Base']:
                if col in df_vis.columns:
                    df_vis[col] = df_vis[col].apply(lambda x: f"${x:,.2f}" if pd.notna(x) else "—")
            for col in ['% vs Media', 'Ventas vs Flete Factura', '% Flete sobre Facturación']:
                if col in df_vis.columns:
                    df_vis[col] = df_vis[col].apply(lambda x: f"{x:+.1f}%")
            # Orden columnas
            cols_orden = [c for c in ['ID_VIAJE_UNICO','FAC','RUTA','Origen','Destino','Unidad','Tarimas','KG MOVIDOS','Facturación','FLETE FACTURA','Costo por kg','Ventas vs Flete Factura','MANIOBRAS','REPARTOS','DEMORAS Y ESTADIAS','OTROS','TOTAL FLETE','Diferencia vs Media Base','% vs Media'] if c in df_vis.columns]
            st.dataframe(df_vis[cols_orden], use_container_width=True, hide_index=True)
            # Export
            excel_audit = to_excel_bytes({"Auditoria": df_show})
            st.download_button("📥 Descargar auditoría (Excel)", data=excel_audit, file_name=f"LogiSense_Auditoria_{modo_periodo}_{per_b}_MediaBase.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        else:
            st.success(f"✅ No se encontraron viajes por encima de la tarifa media base en {modo_periodo} {per_b}.")
            st.dataframe(df_b_grouped.head(20), use_container_width=True)

        hover_viajes = [col for col in ['ID_VIAJE_UNICO', 'FAC', 'Unidad', 'Tipo de Embarque', 'Ventas vs Flete Factura'] if col in df_b_grouped.columns]
        fig_sc = px.scatter(
            df_b_grouped, x='KG MOVIDOS', y='FLETE FACTURA', color='RUTA',
            hover_data=hover_viajes, title="Flete vs KG — Outliers resaltados"
        )
        if not df_show.empty:
            fig_sc.add_trace(go.Scatter(x=df_show['KG MOVIDOS'], y=df_show['FLETE FACTURA'], mode='markers', marker=dict(size=14, line=dict(width=2,color='red'), color='rgba(255,0,0,0.15)'), name='Outlier'))
        st.plotly_chart(fig_sc, use_container_width=True)

        fig_costo_kg = px.scatter(
            df_b_grouped, x='KG MOVIDOS', y='Costo por kg', color='RUTA',
            hover_data=hover_viajes, title="Costo por kg por viaje"
        )
        st.plotly_chart(fig_costo_kg, use_container_width=True)

        st.markdown("### Gráfico de eficiencia")
        capacidades_ideales = {
            'TRAILER': 22000,
            'TORTON': 11000,
            'RABON': 8000,
            'CAMIONETA 3.5': 3500,
            'CAMIONETA 1.5': 1500
        }
        # Para eficiencia, los fletes CONSOLIDADO se evalúan como una sola unidad
        # de carga. El resto de la auditoría conserva df_b_grouped sin cambios.
        def _unir_valores(serie):
            valores = pd.Series(serie).dropna().astype(str).str.strip()
            valores = valores[
                valores.ne('') & valores.str.upper().ne('NAN') & valores.str.upper().ne('NONE')
            ]
            return ', '.join(pd.unique(valores))

        columnas_eficiencia = {
            'ID_VIAJE_UNICO': _unir_valores,
            'FAC': _unir_facturas,
            'UNIDAD_CONSOLIDADO': 'first',
            'TIPO_UNIDAD_EFICIENCIA': 'first',
            'RUTA_ORIGINAL': 'first',
            'CARTA_PORTE_CONSOLIDADO': 'first',
            'METODO_AGRUPACION_EFICIENCIA': 'first',
            'ES_FLETE_CONSOLIDADO': 'max',
            'KG MOVIDOS': 'sum'
        }
        columnas_eficiencia = {
            col: funcion for col, funcion in columnas_eficiencia.items()
            if col in df_bv.columns
        }
        df_eficiencia = (
            df_bv.groupby('ID_GRUPO_EFICIENCIA', dropna=False)
            .agg(columnas_eficiencia)
            .reset_index()
            if 'ID_GRUPO_EFICIENCIA' in df_bv.columns
            else df_b_grouped.copy()
        )
        df_eficiencia = df_eficiencia.rename(columns={
            'UNIDAD_CONSOLIDADO': 'Unidad operativa',
            'TIPO_UNIDAD_EFICIENCIA': 'Unidad',
            'RUTA_ORIGINAL': 'RUTA',
            'CARTA_PORTE_CONSOLIDADO': 'Carta porte',
            'METODO_AGRUPACION_EFICIENCIA': 'Método de agrupación'
        })
        if 'ID_GRUPO_EFICIENCIA' in df_eficiencia.columns:
            df_eficiencia['Viaje / consolidado'] = df_eficiencia['ID_GRUPO_EFICIENCIA'].str.replace(
                'CONSOLIDADO_CP_', 'CP: ', regex=False
            ).str.replace(
                'CONSOLIDADO_SECUENCIA_', 'Consolidado consecutivo: ', regex=False
            ).str.replace(
                'VIAJE_', 'Viaje: ', regex=False
            )
        unidades_eficiencia = (
            df_eficiencia['Unidad']
            if 'Unidad' in df_eficiencia.columns
            else pd.Series('', index=df_eficiencia.index)
        )
        df_eficiencia['Capacidad ideal (kg)'] = unidades_eficiencia.astype(str).str.strip().str.upper().map(capacidades_ideales)
        df_eficiencia['Eficiencia (%)'] = np.where(
            df_eficiencia['Capacidad ideal (kg)'].notna() & (df_eficiencia['Capacidad ideal (kg)'] > 0),
            df_eficiencia['KG MOVIDOS'] / df_eficiencia['Capacidad ideal (kg)'] * 100,
            0
        )
        df_eficiencia_grafico = df_eficiencia[df_eficiencia['Capacidad ideal (kg)'].notna()].copy()
        if not df_eficiencia_grafico.empty:
            fig_eficiencia = px.bar(
                df_eficiencia_grafico.sort_values('Eficiencia (%)'),
                x='Viaje / consolidado', y='Eficiencia (%)', color='Unidad',
                hover_data=[col for col in [
                    'ID_VIAJE_UNICO', 'FAC', 'Carta porte', 'Método de agrupación',
                    'Unidad operativa', 'KG MOVIDOS', 'Capacidad ideal (kg)', 'RUTA'
                ] if col in df_eficiencia_grafico.columns],
                title="Eficiencia de carga por viaje o flete consolidado"
            )
            fig_eficiencia.add_hline(y=100, line_dash='dash', line_color='red', annotation_text='100%')
            st.plotly_chart(fig_eficiencia, use_container_width=True)

            viajes_eficiencia_baja = df_eficiencia_grafico[df_eficiencia_grafico['Eficiencia (%)'] < 100].sort_values('Eficiencia (%)')
            st.markdown("#### Viajes con eficiencia menor al 100%")
            if not viajes_eficiencia_baja.empty:
                st.dataframe(
                    viajes_eficiencia_baja[[col for col in [
                        'Viaje / consolidado', 'ID_VIAJE_UNICO', 'FAC', 'Carta porte',
                        'Método de agrupación', 'Unidad operativa', 'Unidad', 'RUTA', 'KG MOVIDOS',
                        'Capacidad ideal (kg)', 'Eficiencia (%)'
                    ] if col in viajes_eficiencia_baja.columns]],
                    use_container_width=True,
                    hide_index=True
                )
            else:
                st.success("✅ No hay viajes con eficiencia menor al 100% en los tipos de transporte especificados.")
        else:
            st.info("No hay viajes con un tipo de transporte incluido en las capacidades definidas.")

        st.markdown("### Viajes TR2 con flete superior al 5.7% de la facturación")
        # Aplica exactamente la misma lógica de agrupación usada en eficiencia:
        # CONSOLIDADO con carta porte se consolida por CP; sin CP, solo se une
        # cuando unidad y transportista coinciden en filas consecutivas.
        # Así se evalúa el porcentaje sobre la facturación total del flete real,
        # no sobre cada registro individual que lo compone.
        columnas_tr2 = {
            'ID_VIAJE_UNICO': _unir_valores,
            'FAC': _unir_facturas,
            'TRANSPORTISTA_CONSOLIDADO': 'first',
            'UNIDAD_CONSOLIDADO': 'first',
            'TIPO_UNIDAD_EFICIENCIA': 'first',
            'TIPO DE EMBARQUE': _unir_valores,
            'RUTA_ORIGINAL': 'first',
            'CARTA_PORTE_CONSOLIDADO': 'first',
            'METODO_AGRUPACION_EFICIENCIA': 'first',
            'ES_FLETE_CONSOLIDADO': 'max',
            'IMPORTE FACTURADO SIN IVA': 'sum',
            'FLETE FACTURA': 'sum'
        }
        columnas_tr2 = {
            col: funcion for col, funcion in columnas_tr2.items()
            if col in df_bv.columns
        }
        df_tr2_agrupado = (
            df_bv.groupby('ID_GRUPO_EFICIENCIA', dropna=False)
            .agg(columnas_tr2)
            .reset_index()
            if 'ID_GRUPO_EFICIENCIA' in df_bv.columns
            else df_b_grouped.copy()
        )
        df_tr2_agrupado = df_tr2_agrupado.rename(columns={
            'TRANSPORTISTA_CONSOLIDADO': 'Transportista',
            'UNIDAD_CONSOLIDADO': 'Unidad operativa',
            'TIPO_UNIDAD_EFICIENCIA': 'Unidad',
            'TIPO DE EMBARQUE': 'Tipo de Embarque',
            'RUTA_ORIGINAL': 'RUTA',
            'CARTA_PORTE_CONSOLIDADO': 'Carta porte',
            'METODO_AGRUPACION_EFICIENCIA': 'Método de agrupación',
            'IMPORTE FACTURADO SIN IVA': 'Facturación'
        })
        if 'ID_GRUPO_EFICIENCIA' in df_tr2_agrupado.columns:
            df_tr2_agrupado['Viaje / consolidado'] = df_tr2_agrupado['ID_GRUPO_EFICIENCIA'].str.replace(
                'CONSOLIDADO_CP_', 'CP: ', regex=False
            ).str.replace(
                'CONSOLIDADO_SECUENCIA_', 'Consolidado consecutivo: ', regex=False
            ).str.replace(
                'VIAJE_', 'Viaje: ', regex=False
            )
        else:
            df_tr2_agrupado['Viaje / consolidado'] = (
                'Viaje: ' + df_tr2_agrupado['ID_VIAJE_UNICO'].astype(str)
            )

        df_tr2_agrupado['% Flete sobre Facturación'] = np.where(
            df_tr2_agrupado.get('Facturación', 0) > 0,
            df_tr2_agrupado.get('FLETE FACTURA', 0) / df_tr2_agrupado['Facturación'] * 100,
            0
        )
        tipos_embarque = (
            df_tr2_agrupado['Tipo de Embarque']
            if 'Tipo de Embarque' in df_tr2_agrupado.columns
            else pd.Series('', index=df_tr2_agrupado.index)
        )
        es_tr2 = tipos_embarque.astype(str).str.split(', ').apply(
            lambda tipos: 'TR2' in {str(tipo).strip().upper() for tipo in tipos}
        )
        viajes_tr2_flete_alto = df_tr2_agrupado[
            es_tr2 & (df_tr2_agrupado['% Flete sobre Facturación'] > 5.7)
        ].sort_values('% Flete sobre Facturación', ascending=False)
        if not viajes_tr2_flete_alto.empty:
            st.dataframe(
                viajes_tr2_flete_alto[[col for col in [
                    'Viaje / consolidado', 'ID_VIAJE_UNICO', 'FAC', 'Carta porte',
                    'Método de agrupación', 'Transportista', 'Unidad operativa',
                    'Unidad', 'RUTA', 'Tipo de Embarque', 'Facturación',
                    'FLETE FACTURA', '% Flete sobre Facturación'
                ] if col in viajes_tr2_flete_alto.columns]],
                use_container_width=True,
                hide_index=True
            )
        else:
            st.info("No hay viajes TR2 con flete superior al 5.7% de la facturación.")

        # Comparativo adicional contra la mediana del periodo base.
        st.markdown("---")
        mediana_ref = mediana_viaje_a
        viajes_altos_mediana = df_b_grouped[df_b_grouped['FLETE FACTURA'] > mediana_ref].copy()
        viajes_altos_mediana['Diferencia vs Mediana Base'] = viajes_altos_mediana['FLETE FACTURA'] - mediana_ref
        viajes_altos_mediana['% vs Mediana'] = np.where(
            mediana_ref > 0,
            (viajes_altos_mediana['FLETE FACTURA'] - mediana_ref) / mediana_ref * 100,
            0
        )
        df_show_mediana = viajes_altos_mediana.sort_values('FLETE FACTURA', ascending=False)
        st.metric(
            f"Tarifa mediana base — {periodo_base_audit}",
            f"${mediana_ref:,.2f}",
            help=f"Valor central de FLETE FACTURA por viaje en {periodo_base_audit}."
        )
        st.metric(
            f"Viajes de {periodo_actual_audit} por encima de la mediana base",
            f"{len(df_show_mediana)} de {len(df_b_grouped)}",
            delta=f"{len(df_show_mediana)/len(df_b_grouped)*100:.1f}%" if len(df_b_grouped)>0 else None
        )
        st.caption(
            "Importancia de la comparación con la mediana: permite identificar viajes por "
            "encima del costo típico, sin que los valores extremos distorsionen la referencia."
        )

        if not df_show_mediana.empty:
            df_vis_mediana = df_show_mediana.copy()
            cols_dinero_mediana = ['Facturación','FLETE FACTURA','MANIOBRAS','REPARTOS','DEMORAS Y ESTADIAS','OTROS','TOTAL FLETE']
            for col in cols_dinero_mediana + ['Diferencia vs Mediana Base']:
                if col in df_vis_mediana.columns:
                    df_vis_mediana[col] = df_vis_mediana[col].apply(
                        lambda x: f"${x:,.2f}" if pd.notna(x) else "—"
                    )
            if '% vs Mediana' in df_vis_mediana.columns:
                df_vis_mediana['% vs Mediana'] = df_vis_mediana['% vs Mediana'].apply(
                    lambda x: f"{x:+.1f}%"
                )
            cols_orden_mediana = [c for c in [
                'ID_VIAJE_UNICO','RUTA','Origen','Destino','Unidad','Tarimas','KG MOVIDOS',
                'Facturación','FLETE FACTURA','MANIOBRAS','REPARTOS','DEMORAS Y ESTADIAS',
                'OTROS','TOTAL FLETE','Diferencia vs Mediana Base','% vs Mediana'
            ] if c in df_vis_mediana.columns]
            st.dataframe(df_vis_mediana[cols_orden_mediana], use_container_width=True, hide_index=True)
        else:
            st.success(f"✅ No se encontraron viajes por encima de la tarifa mediana base en {modo_periodo} {per_b}.")

with tab3:
    var_tot = _var_pct(tot_b, tot_a)
    prompt_texto = f"""Actúa como un Gerente Senior de Logística y Cadena de Suministro.
Analiza la siguiente variación de fletes, ventas e imprevistos financieros y genera un reporte ejecutivo.

DATOS COMPARATIVOS ({modo_periodo.upper()} {per_a} vs {modo_periodo.upper()} {per_b}):
- Periodo Base ({modo_periodo} {per_a}): Facturación Venta: ${fact_a:,.2f} | Viajes Reales: {viajes_a} | KG Movidos: {kg_a:,.0f} | Tarimas: {tar_a:,.0f} | Costo Puro/KG: ${costo_kg_a:,.2f} | Costo Puro/Tarima: ${costo_tar_a:,.2f} | Tarifa Media/Viaje: ${media_viaje_a:,.2f} | Gasto Operación Total: ${tot_a:,.2f}
- Periodo Actual ({modo_periodo} {per_b}): Facturación Venta: ${fact_b:,.2f} | Viajes Reales: {viajes_b} | KG Movidos: {kg_b:,.0f} | Tarimas: {tar_b:,.0f} | Costo Puro/KG: ${costo_kg_b:,.2f} | Costo Puro/Tarima: ${costo_tar_b:,.2f} | Tarifa Media/Viaje: ${media_viaje_b:,.2f} | Gasto Operación Total: ${tot_b:,.2f}
- Variación en Facturación de Ventas: {var_fact:+.2f}%
- Variación del Gasto Total de la Operación: {var_tot:+.2f}%
- Filtros Operativos -> Cliente: {clientes_sel if clientes_sel else 'Todos'} | Transportista: {transp_sel if transp_sel else 'Todos'} | Tipo de Transporte: {tipo_trans_sel if tipo_trans_sel else 'Todos'} | Tipo de Embarque: {embarque_sel if embarque_sel else 'Todos'} | Origen: {origen_sel if origen_sel else 'Todos'} | Destino: {destino_sel if destino_sel else 'Todos'}

ESTRUCTURA DEL REPORTE SOLICITADA:
1. 📌 Resumen Ejecutivo
2. 🚨 Alertas Operativas (Relación Ventas vs Costos de Fletes, Desviación en Tarifa Base por Viaje, Costo por KG y Tarimas)
3. 💡 Recomendaciones para Negociación de Tarifas y Eficiencia en Costos Variables"""
    st.code(prompt_texto, language="markdown")
    st.download_button("📋 Descargar prompt (.txt)", data=prompt_texto.encode('utf-8'), file_name=f"LogiSense_Prompt_{modo_periodo}_{per_a}_vs_{per_b}.txt")

    st.markdown("---")
    st.subheader("✨ Reporte ejecutivo con IA")
    st.caption(
        "Claude es la opción principal. Si Claude no está disponible o presenta un error, "
        "la app intenta generar el reporte con OpenAI como respaldo."
    )

    with st.expander("🔐 Configurar claves y modelo", expanded=False):
        st.info(
            "Para producción, guarda las claves como secretos de Streamlit o variables de entorno. "
            "Las claves pegadas aquí solo se usan durante esta sesión y no se exportan."
        )
        clave_claude_temporal = st.text_input(
            "Clave API de Anthropic / Claude",
            type="password",
            placeholder="sk-ant-…",
            help="Alternativamente, configura ANTHROPIC_API_KEY en secrets.toml o como variable de entorno."
        )
        modelo_claude = st.text_input(
            "Modelo principal de Claude",
            value="claude-sonnet-4-20250514"
        )
        clave_openai_temporal = st.text_input(
            "Clave API de OpenAI (respaldo)",
            type="password",
            placeholder="sk-…",
            help="Alternativamente, configura OPENAI_API_KEY en secrets.toml o como variable de entorno."
        )
        modelo_openai = st.text_input(
            "Modelo de respaldo OpenAI",
            value="gpt-4.1-mini"
        )

    clave_claude = clave_claude_temporal.strip() or _obtener_clave_ia("ANTHROPIC_API_KEY")
    clave_openai = clave_openai_temporal.strip() or _obtener_clave_ia("OPENAI_API_KEY")

    if st.button("🤖 Generar reporte ejecutivo con IA", type="primary", use_container_width=True):
        try:
            with st.spinner("Analizando los indicadores logísticos con IA..."):
                reporte_ia, proveedor_ia = generar_reporte_ia(
                    prompt=prompt_texto,
                    clave_claude=clave_claude,
                    clave_openai=clave_openai,
                    modelo_claude=modelo_claude.strip(),
                    modelo_openai=modelo_openai.strip()
                )
            st.session_state["reporte_ia_logisense"] = reporte_ia
            st.session_state["proveedor_ia_logisense"] = proveedor_ia
        except Exception as exc:
            st.error(f"❌ No se pudo generar el reporte: {exc}")

    if "reporte_ia_logisense" in st.session_state:
        st.success(f"Reporte generado con {st.session_state['proveedor_ia_logisense']}.")
        st.markdown(st.session_state["reporte_ia_logisense"])
        st.download_button(
            "📥 Descargar reporte de IA (.md)",
            data=st.session_state["reporte_ia_logisense"].encode("utf-8"),
            file_name=f"LogiSense_Reporte_IA_{modo_periodo}_{per_a}_vs_{per_b}.md",
            mime="text/markdown"
        )
    else:
        st.caption("También puedes copiar el prompt y usarlo manualmente en Claude, ChatGPT o Gemini.")

st.markdown('<div class="footer-v21">Desarrollado por José Daniel Maldonado Flores — LogiSense AI v2.1</div>', unsafe_allow_html=True)
