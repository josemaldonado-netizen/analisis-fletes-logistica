import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px

st.set_page_config(page_title="LogiSense AI", layout="wide")
st.title("🚚 LogiSense AI — Analítica Logística Avanzada")

st.markdown(
    """
    <style>
    .footer {
        position: fixed;
        right: 150px;
        bottom: 12px;
        background-color: transparent;
        color: #a0a0a0;
        text-align: right;
        font-size: 12px;
        font-style: italic;
        z-index: 100;
        pointer-events: none;
    }
    </style>
    <div class="footer">Desarrollado por José Daniel Maldonado Flores</div>
    """,
    unsafe_allow_html=True
)

# Ocultar menú principal, footer nativo y botón 'Manage app'
st.markdown(
    """
    <style>
    /* Ocultar el menú de 3 puntos en la esquina superior derecha */
    #MainMenu {visibility: hidden;}
    
    /* Ocultar el footer por defecto de Streamlit ("Made with Streamlit") */
    footer {visibility: hidden;}
    
    /* Ocultar el encabezado superior (incluye botón de deploy/manage) */
    header {visibility: hidden;}
    
    /* Ocultar específicamente el contenedor flotante de Manage app */
    div[data-testid="stStatusWidget"] {visibility: hidden;}
    .viewerBadge_container__1tB24 {display: none !important;}
    </style>
    """,
    unsafe_allow_html=True
)

# 1. Componente para subir el archivo (Excel o CSV)
archivo_subido = st.file_uploader(
    "📁 Carga tu archivo de datos (Excel .xlsx o CSV)",
    type=["xlsx", "csv"]
)

# Lista estándar de meses para validar, filtrar y ORDENAR cronológicamente
NOMBRES_MESES = [
    'ENERO', 'FEBRERO', 'MARZO', 'ABRIL', 'MAYO', 'JUNIO', 
    'JULIO', 'AGOSTO', 'SEPTIEMBRE', 'OCTUBRE', 'NOVIEMBRE', 'DICIEMBRE'
]

@st.cache_data
def procesar_archivo(file):
    lista_dfs = []

    if file.name.endswith('.xlsx'):
        excel_file = pd.ExcelFile(file)
        for nombre_hoja in NOMBRES_MESES:
            if nombre_hoja in excel_file.sheet_names:
                df_temp = pd.read_excel(excel_file, sheet_name=nombre_hoja, header=0)
                if not df_temp.empty:
                    df_temp.columns = [str(h).strip() for h in df_temp.columns]
                    df_temp['MES_ORIGEN'] = nombre_hoja
                    lista_dfs.append(df_temp)
    else:
        df_temp = pd.read_csv(file, header=0)
        df_temp.columns = [str(h).strip() for h in df_temp.columns]
        lista_dfs.append(df_temp)

    if not lista_dfs:
        return pd.DataFrame()

    df_out = pd.concat(lista_dfs, ignore_index=True)

    # LIMPIEZA RIGUROSA DE INDICE VIAJES
    if 'INDICE VIAJES' in df_out.columns:
        idx_limpio = df_out['INDICE VIAJES'].astype(str).str.strip().str.upper()
        idx_limpio = idx_limpio.replace(['NA', 'N/A', 'NONE', 'NAN', 'UNDEFINED', 'NULL', ''], np.nan)
        indices_numericos = pd.to_numeric(idx_limpio, errors='coerce')
        
        df_out = df_out[indices_numericos.notna() & (indices_numericos > 0)].copy()
        df_out['ID_VIAJE_UNICO'] = indices_numericos[indices_numericos.notna() & (indices_numericos > 0)].astype(int)
        df_out['ES_CUENTA_VIAJE'] = True
    else:
        st.warning("⚠️ No se encontró la columna 'INDICE VIAJES' en el archivo cargado.")
        df_out['ID_VIAJE_UNICO'] = df_out.index
        df_out['ES_CUENTA_VIAJE'] = True

    # Normalización de FECHA FACTURA a datetime
    if 'FECHA FACTURA' in df_out.columns:
        df_out['FECHA_FACTURA_DT'] = pd.to_datetime(df_out['FECHA FACTURA'], errors='coerce')

    # Normalización de Semana y Mes
    col_sem = 'SEMANA CALENDARIO FACTURA' if 'SEMANA CALENDARIO FACTURA' in df_out.columns else 'SEMANA CALENDARIO PEDIDO'
    if col_sem in df_out.columns:
        df_out['SEMANA_ANALISIS'] = pd.to_numeric(df_out[col_sem], errors='coerce')

    if 'MES FACTURA' not in df_out.columns:
        df_out['MES FACTURA'] = df_out['MES_ORIGEN']

    # Limpieza numérica de montos y cantidades
    cols_a_limpiar = [
        'IMPORTE FACTURADO SIN IVA', 'KG MOVIDOS', 'FLETE FACTURA',
        'MANIOBRAS', 'REPARTOS', 'DEMORAS Y ESTADIAS', 'OTROS',
        'TOTAL FLETE', 'KM RECORRIDOS', 'TARIMAS TOTALES POR VIAJE'
    ]

    for c in cols_a_limpiar:
        if c in df_out.columns:
            df_out[c] = df_out[c].astype(str).str.replace('$', '', regex=False).str.replace(',', '', regex=False).str.strip()
            df_out[c] = pd.to_numeric(df_out[c], errors='coerce').fillna(0)

    return df_out

def render_kpi(label, val_a, val_b, delta_txt, is_positive_good=True, val_num=0):
    color_badge = "#28a745" if (val_num >= 0 if is_positive_good else val_num <= 0) else "#dc3545"
    bg_badge = "#e6f4ea" if (val_num >= 0 if is_positive_good else val_num <= 0) else "#fce8e6"

    st.markdown(f"""
        <div style="background-color: #f8f9fa; border: 1px solid #e9ecef; border-radius: 8px; padding: 12px; height: 100%;">
            <p style="font-size: 13px; color: #5f6368; margin-bottom: 4px; font-weight: bold; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">{label}</p>
            <p style="font-size: 17px; font-weight: bold; color: #202124; margin-bottom: 6px; word-break: break-word;">
                {val_a} <span style="color: #70757a;">➜</span> {val_b}
            </p>
            <span style="background-color: {bg_badge}; color: {color_badge}; font-size: 12px; font-weight: bold; padding: 2px 8px; border-radius: 12px;">
                {delta_txt}
            </span>
        </div>
    """, unsafe_allow_html=True)

# Carga y procesamiento del archivo
if archivo_subido is not None:
    with st.spinner("⏳ Procesando datos del archivo..."):
        df_raw = procesar_archivo(archivo_subido)

    if not df_raw.empty:
        st.success("✅ Base de datos cargada y filtrada dinámicamente (se omitieron filas sin 'INDICE VIAJES').")

        # BARRA LATERAL: FILTROS DE ANÁLISIS
        st.sidebar.header("🔍 Filtros Operativos")

        # FILTRO POR FECHA FACTURA (Calendario)
        if 'FECHA_FACTURA_DT' in df_raw.columns and df_raw['FECHA_FACTURA_DT'].notna().any():
            fechas_validas = df_raw['FECHA_FACTURA_DT'].dropna()
            min_fecha = fechas_validas.min().date()
            max_fecha = fechas_validas.max().date()

            activar_filtro_fecha = st.sidebar.checkbox("Filtrar por Día (FECHA FACTURA)")
            fecha_sel = None
            if activar_filtro_fecha:
                fecha_sel = st.sidebar.date_input(
                    "Selecciona Fecha Factura",
                    value=min_fecha,
                    min_value=min_fecha,
                    max_value=max_fecha
                )

        col_cliente = 'CLIENTE'
        opciones_cliente = sorted([str(x) for x in df_raw[col_cliente].dropna().unique()]) if col_cliente in df_raw.columns else []
        clientes_sel = st.sidebar.multiselect("Cliente(s)", opciones_cliente, default=[])

        col_transp = 'TRANSPORTISTA'
        opciones_transp = sorted([str(x) for x in df_raw[col_transp].dropna().unique()]) if col_transp in df_raw.columns else []
        transp_sel = st.sidebar.multiselect("Transportista(s)", opciones_transp, default=[])

        col_tipo_trans = 'TIPO DE TRANSPORTE'
        opciones_tipo_trans = sorted([str(x) for x in df_raw[col_tipo_trans].dropna().unique()]) if col_tipo_trans in df_raw.columns else []
        tipo_trans_sel = st.sidebar.multiselect("Tipo de Transporte", opciones_tipo_trans, default=[])

        col_embarque = 'TIPO DE EMBARQUE'
        opciones_embarque = sorted([str(x) for x in df_raw[col_embarque].dropna().unique()]) if col_embarque in df_raw.columns else []
        embarque_sel = st.sidebar.multiselect("Tipo de Embarque", opciones_embarque, default=[])

        col_origen = 'ORIGEN DE VIAJE'
        opciones_origen = sorted([str(x) for x in df_raw[col_origen].dropna().unique()]) if col_origen in df_raw.columns else []
        origen_sel = st.sidebar.multiselect("Origen", opciones_origen, default=[])

        col_destino = 'DESTINO DE EMBARQUE'
        opciones_destino = sorted([str(x) for x in df_raw[col_destino].dropna().unique()]) if col_destino in df_raw.columns else []
        destino_sel = st.sidebar.multiselect("Destino", opciones_destino, default=[])

        # Aplicar filtros operativos múltiples
        df = df_raw.copy()

        if 'FECHA_FACTURA_DT' in df.columns and activar_filtro_fecha and fecha_sel:
            df = df[df['FECHA_FACTURA_DT'].dt.date == fecha_sel]

        if clientes_sel and col_cliente in df.columns:
            df = df[df[col_cliente].astype(str).isin(clientes_sel)]
        if transp_sel and col_transp in df.columns:
            df = df[df[col_transp].astype(str).isin(transp_sel)]
        if tipo_trans_sel and col_tipo_trans in df.columns:
            df = df[df[col_tipo_trans].astype(str).isin(tipo_trans_sel)]
        if embarque_sel and col_embarque in df.columns:
            df = df[df[col_embarque].astype(str).isin(embarque_sel)]
        if origen_sel and col_origen in df.columns:
            df = df[df[col_origen].astype(str).isin(origen_sel)]
        if destino_sel and col_destino in df.columns:
            df = df[df[col_destino].astype(str).isin(destino_sel)]

        # SELECCIÓN DE PERIODO
        st.sidebar.markdown("---")
        st.sidebar.header("📅 Periodos de Comparación")
        modo_periodo = st.sidebar.radio("Comparar por:", ["Semana", "Mes"])

        if modo_periodo == "Semana":
            col_periodo = 'SEMANA_ANALISIS'
            periodos = sorted([int(x) for x in df[col_periodo].dropna().unique()])
            label_a, label_b = "Semana A (Base)", "Semana B (Actual)"
        else:
            col_periodo = 'MES FACTURA'
            meses_existentes = df[col_periodo].dropna().unique()
            periodos = [m for m in NOMBRES_MESES if m in meses_existentes]
            label_a, label_b = "Mes A (Base)", "Mes B (Actual)"

        if len(periodos) > 0:
            c_per1, c_per2 = st.sidebar.columns(2)
            per_a = c_per1.selectbox(label_a, periodos, index=0)
            per_b = c_per2.selectbox(label_b, periodos, index=min(1, len(periodos)-1))

            df_a = df[df[col_periodo] == per_a]
            df_b = df[df[col_periodo] == per_b]

            # TABS DE VISUALIZACIÓN
            tab1, tab2, tab3 = st.tabs(["📊 Comparativo Financiero y Gráficos", "🚨 Auditoría de Anomalías", "📝 Prompt para IA Executive"])

            with tab1:
                st.subheader(f"📊 Comparativa Real: {modo_periodo} {per_a} vs {modo_periodo} {per_b}")

                df_a_principales = df_a[df_a['ES_CUENTA_VIAJE'] == True]
                df_b_principales = df_b[df_b['ES_CUENTA_VIAJE'] == True]

                viajes_a = df_a_principales['ID_VIAJE_UNICO'].dropna().nunique()
                viajes_b = df_b_principales['ID_VIAJE_UNICO'].dropna().nunique()

                tot_a = df_a['TOTAL FLETE'].sum() if 'TOTAL FLETE' in df_a.columns else 0
                tot_b = df_b['TOTAL FLETE'].sum() if 'TOTAL FLETE' in df_b.columns else 0

                fact_a = df_a['IMPORTE FACTURADO SIN IVA'].sum() if 'IMPORTE FACTURADO SIN IVA' in df_a.columns else 0
                fact_b = df_b['IMPORTE FACTURADO SIN IVA'].sum() if 'IMPORTE FACTURADO SIN IVA' in df_b.columns else 0

                flete_puro_a = df_a['FLETE FACTURA'].sum() if 'FLETE FACTURA' in df_a.columns else 0
                flete_puro_b = df_b['FLETE FACTURA'].sum() if 'FLETE FACTURA' in df_b.columns else 0

                media_viaje_a = (flete_puro_a / viajes_a) if viajes_a > 0 else 0
                media_viaje_b = (flete_puro_b / viajes_b) if viajes_b > 0 else 0

                kg_a = df_a['KG MOVIDOS'].sum() if 'KG MOVIDOS' in df_a.columns else 0
                kg_b = df_b['KG MOVIDOS'].sum() if 'KG MOVIDOS' in df_b.columns else 0

                tar_a = df_a['TARIMAS TOTALES POR VIAJE'].sum() if 'TARIMAS TOTALES POR VIAJE' in df_a.columns else 0
                tar_b = df_b['TARIMAS TOTALES POR VIAJE'].sum() if 'TARIMAS TOTALES POR VIAJE' in df_b.columns else 0

                costo_kg_a = (flete_puro_a / kg_a) if kg_a > 0 else 0
                costo_kg_b = (flete_puro_b / kg_b) if kg_b > 0 else 0

                costo_tar_a = (flete_puro_a / tar_a) if tar_a > 0 else 0
                costo_tar_b = (flete_puro_b / tar_b) if tar_b > 0 else 0

                var_viajes = viajes_b - viajes_a
                var_fact = ((fact_b - fact_a) / fact_a * 100) if fact_a > 0 else 0
                var_costo = ((media_viaje_b - media_viaje_a) / media_viaje_a * 100) if media_viaje_a > 0 else 0
                var_kg = ((kg_b - kg_a) / kg_a * 100) if kg_a > 0 else 0
                var_tar = ((tar_b - tar_a) / tar_a * 100) if tar_a > 0 else 0
                var_costo_kg = ((costo_kg_b - costo_kg_a) / costo_kg_a * 100) if costo_kg_a > 0 else 0
                var_costo_tar = ((costo_tar_b - costo_tar_a) / costo_tar_a * 100) if costo_tar_a > 0 else 0

                st.markdown("### 📐 Resumen de Indicadores Clave")
                
                # Primera fila de KPIs
                m_col1, m_col2, m_col3, m_col4 = st.columns(4)
                with m_col1:
                    render_kpi(
                        f"Facturación Venta ({modo_periodo} {per_a} ➜ {per_b})",
                        f"${fact_a:,.2f}", f"${fact_b:,.2f}",
                        f"{var_fact:+.1f}%",
                        is_positive_good=True, val_num=var_fact
                    )
                with m_col2:
                    render_kpi(
                        f"Total Viajes Reales ({modo_periodo} {per_a} ➜ {per_b})",
                        f"{viajes_a}", f"{viajes_b}",
                        f"{var_viajes:+} viajes",
                        is_positive_good=True, val_num=var_viajes
                    )
                with m_col3:
                    render_kpi(
                        f"Tarifa Media / Viaje ({modo_periodo} {per_a} ➜ {per_b})",
                        f"${media_viaje_a:,.2f}", f"${media_viaje_b:,.2f}",
                        f"{var_costo:+.1f}%",
                        is_positive_good=False, val_num=var_costo
                    )
                with m_col4:
                    render_kpi(
                        f"KG Movidos ({modo_periodo} {per_a} ➜ {per_b})",
                        f"{kg_a:,.0f} kg", f"{kg_b:,.0f} kg",
                        f"{var_kg:+.1f}%",
                        is_positive_good=True, val_num=var_kg
                    )

                st.markdown("<br>", unsafe_allow_html=True)
                
                # Segunda fila de KPIs
                m_col5, m_col6, m_col7 = st.columns(3)
                with m_col5:
                    render_kpi(
                        f"Tarimas Totales ({modo_periodo} {per_a} ➜ {per_b})",
                        f"{tar_a:,.0f}", f"{tar_b:,.0f}",
                        f"{var_tar:+.1f}%",
                        is_positive_good=True, val_num=var_tar
                    )
                with m_col6:
                    render_kpi(
                        f"Costo por Kilogramo ({modo_periodo} {per_a} ➜ {per_b})",
                        f"${costo_kg_a:,.2f}", f"${costo_kg_b:,.2f}",
                        f"{var_costo_kg:+.1f}%",
                        is_positive_good=False, val_num=var_costo_kg
                    )
                with m_col7:
                    render_kpi(
                        f"Costo por Tarima ({modo_periodo} {per_a} ➜ {per_b})",
                        f"${costo_tar_a:,.2f}", f"${costo_tar_b:,.2f}",
                        f"{var_costo_tar:+.1f}%",
                        is_positive_good=False, val_num=var_costo_tar
                    )

                # GRÁFICO DE LÍNEAS
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
                    "Costo por Tarima": "COSTO_TARIMA"
                }

                metricas_seleccionadas = st.multiselect(
                    "Selecciona las métricas para graficar:",
                    options=list(dict_metricas.keys()),
                    default=["Facturación (Ventas)", "Total Flete (Costo)"]
                )

                df_trend = df.copy()
                if modo_periodo == "Mes":
                    df_trend['PERIODO_ORDEN'] = pd.Categorical(df_trend['MES FACTURA'], categories=NOMBRES_MESES, ordered=True)
                    df_grouped = df_trend.groupby('PERIODO_ORDEN', observed=True).agg({
                        'IMPORTE FACTURADO SIN IVA': 'sum', 'FLETE FACTURA': 'sum', 
                        'MANIOBRAS': 'sum', 'REPARTOS': 'sum', 'DEMORAS Y ESTADIAS': 'sum', 
                        'OTROS': 'sum', 'TOTAL FLETE': 'sum', 'KG MOVIDOS': 'sum', 
                        'TARIMAS TOTALES POR VIAJE': 'sum'
                    }).reset_index().rename(columns={'PERIODO_ORDEN': 'Periodo'})
                else:
                    df_grouped = df_trend.groupby('SEMANA_ANALISIS').agg({
                        'IMPORTE FACTURADO SIN IVA': 'sum', 'FLETE FACTURA': 'sum', 
                        'MANIOBRAS': 'sum', 'REPARTOS': 'sum', 'DEMORAS Y ESTADIAS': 'sum', 
                        'OTROS': 'sum', 'TOTAL FLETE': 'sum', 'KG MOVIDOS': 'sum', 
                        'TARIMAS TOTALES POR VIAJE': 'sum'
                    }).reset_index().rename(columns={'SEMANA_ANALISIS': 'Periodo'})

                df_grouped['COSTO_KG'] = np.where(df_grouped['KG MOVIDOS'] > 0, df_grouped['FLETE FACTURA'] / df_grouped['KG MOVIDOS'], 0)
                df_grouped['COSTO_TARIMA'] = np.where(df_grouped['TARIMAS TOTALES POR VIAJE'] > 0, df_grouped['FLETE FACTURA'] / df_grouped['TARIMAS TOTALES POR VIAJE'], 0)

                if metricas_seleccionadas and not df_grouped.empty:
                    cols_y = [dict_metricas[m] for m in metricas_seleccionadas]
                    fig_lineas = px.line(
                        df_grouped, x='Periodo', y=cols_y, markers=True,
                        title=f"Evolución por {modo_periodo}",
                        labels={'value': 'Monto ($) / Cantidad', 'variable': 'Métrica'}
                    )
                    fig_lineas.update_layout(hovermode="x unified", legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
                    st.plotly_chart(fig_lineas, use_container_width=True)

                st.markdown("---")
                st.markdown("### 💵 Desglose de Gastos Acumulados")

                conceptos = ['FLETE FACTURA', 'MANIOBRAS', 'REPARTOS', 'DEMORAS Y ESTADIAS', 'OTROS', 'TOTAL FLETE']
                filas = []

                for conc in conceptos:
                    m_a = df_a[conc].sum() if conc in df_a.columns else 0
                    m_b = df_b[conc].sum() if conc in df_b.columns else 0
                    dif = m_b - m_a
                    pct = ((m_b - m_a) / m_a * 100) if m_a > 0 else 0

                    filas.append({
                        'Línea de Gasto': conc,
                        f'{modo_periodo} {per_a}': f"${m_a:,.2f}",
                        f'{modo_periodo} {per_b}': f"${m_b:,.2f}",
                        'Diferencia ($)': f"${dif:,.2f}",
                        'Variación (%)': f"{pct:+.1f}%"
                    })

                st.table(pd.DataFrame(filas))

            # TAB 2: AUDITORÍA DE ANOMALÍAS
            with tab2:
                df_b_validos = df_b[df_b['ES_CUENTA_VIAJE'] == True]
                
                df_b_grouped = df_b_validos.groupby('ID_VIAJE_UNICO').agg({
                    'CLIENTE': 'first',
                    'TRANSPORTISTA': 'first',
                    'ORIGEN DE VIAJE': 'first',
                    'DESTINO DE EMBARQUE': 'first',
                    'TARIMAS TOTALES POR VIAJE': 'sum',
                    'TIPO DE TRANSPORTE': 'first',
                    'KG MOVIDOS': 'sum',
                    'IMPORTE FACTURADO SIN IVA': 'sum',
                    'FLETE FACTURA': 'sum',
                    'MANIOBRAS': 'sum',
                    'REPARTOS': 'sum',
                    'DEMORAS Y ESTADIAS': 'sum',
                    'OTROS': 'sum',
                    'TOTAL FLETE': 'sum'
                }).reset_index()

                df_b_grouped = df_b_grouped.rename(columns={
                    'ORIGEN DE VIAJE': 'Origen',
                    'DESTINO DE EMBARQUE': 'Destino',
                    'TARIMAS TOTALES POR VIAJE': 'Tarimas',
                    'TIPO DE TRANSPORTE': 'Unidad',
                    'IMPORTE FACTURADO SIN IVA': 'Facturación'
                })

                media_ref = media_viaje_a
                viajes_altos = df_b_grouped[df_b_grouped['FLETE FACTURA'] > media_ref].copy()

                if not viajes_altos.empty:
                    viajes_altos['Diferencia vs Media Base'] = viajes_altos['FLETE FACTURA'] - media_ref
                    viajes_altos = viajes_altos.sort_values(by='FLETE FACTURA', ascending=False)

                    cols_dinero = ['Facturación', 'FLETE FACTURA', 'MANIOBRAS', 'REPARTOS', 'DEMORAS Y ESTADIAS', 'OTROS', 'TOTAL FLETE', 'Diferencia vs Media Base']
                    df_visualizacion = viajes_altos.copy()
                    
                    for col in cols_dinero:
                        if col in df_visualizacion.columns:
                            df_visualizacion[col] = df_visualizacion[col].apply(lambda x: f"${x:,.2f}")

                    st.warning(f"🚨 Auditoría: **{len(viajes_altos)} viajes únicos** en el {modo_periodo} {per_b} superan la tarifa flete base promedio del {modo_periodo} {per_a} (${media_ref:,.2f})")
                    st.dataframe(df_visualizacion, use_container_width=True, hide_index=True)
                else:
                    st.success(f"✅ No se encontraron fletes en el {modo_periodo} {per_b} que superen la media del {modo_periodo} {per_a}.")

            # TAB 3: PROMPT GENERATOR
            with tab3:
                var_tot = ((tot_b - tot_a) / tot_a * 100) if tot_a > 0 else 0

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
        else:
            st.error("No hay suficiente información para comparar con los filtros seleccionados.")
else:
    st.info("👆 Por favor sube tu archivo Excel o CSV para comenzar el análisis.")
