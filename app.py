from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from law_history import LAW_SOURCES, build_event_rows, build_year_rows, load_law_history


st.set_page_config(
    page_title="Lovændringer over tid",
    page_icon="§",
    layout="wide",
)


@st.cache_data(show_spinner=False, ttl=60 * 60 * 24)
def load_histories() -> list[dict]:
    return [load_law_history(source) for source in LAW_SOURCES]


st.title("Lovændringer over tid")
st.caption(
    "Dashboardet viser ændringer pr. år for Helligdagsloven, Udlændingeloven "
    "og Færdselsloven."
)

with st.spinner("Henter og cacher lovhistorik..."):
    histories = load_histories()

year_df = pd.DataFrame(build_year_rows(histories))
event_df = pd.DataFrame(build_event_rows(histories))

if year_df.empty:
    st.error("Der blev ikke fundet ændringsdata for de tre love.")
    st.stop()

law_options = year_df["Lov"].drop_duplicates().tolist()
selected_laws = st.multiselect(
    "Vælg love",
    options=law_options,
    default=law_options,
)

filtered_year_df = year_df[year_df["Lov"].isin(selected_laws)]
filtered_event_df = event_df[event_df["Lov"].isin(selected_laws)]

if filtered_year_df.empty:
    st.warning("Vælg mindst én lov for at se grafen.")
    st.stop()

if not filtered_event_df.empty:
    filtered_event_df = filtered_event_df.copy()
    filtered_event_df["Dato"] = pd.to_datetime(filtered_event_df["Dato"])

col1, col2, col3 = st.columns(3)
col1.metric("Valgte love", len(selected_laws))
col2.metric("Samlede ændringer", int(filtered_year_df["Ændringer"].sum()))
col3.metric(
    "År dækket",
    f"{int(filtered_year_df['År'].min())}–{int(filtered_year_df['År'].max())}",
)

chart = (
    alt.Chart(filtered_year_df)
    .mark_line(point=True, strokeWidth=3)
    .encode(
        x=alt.X("År:Q", axis=alt.Axis(format="d")),
        y=alt.Y("Ændringer:Q", title="Antal ændringer"),
        color=alt.Color("Lov:N", title="Lov"),
        tooltip=["Lov:N", "År:Q", "Ændringer:Q"],
    )
    .properties(height=480)
)

st.altair_chart(chart, use_container_width=True)

st.info(
    "Datagrundlaget kommer direkte fra officielle ELI-metadata hos "
    "Retsinformation (`.rdfa`). Dashboardet finder den tidligst tilgængelige "
    "konsoliderede version via `eli:consolidates`, følger derefter "
    "versionskæden fremad via `eli:consolidated_by` og udleder ændringslove "
    "via `eli:changed_by`. Alle netværkskald caches i 24 timer for at skåne "
    "tjenesten."
)

with st.expander("Datakilder og antagelser", expanded=False):
    for history in histories:
        sources = ", ".join(history["sources_used"]) if history["sources_used"] else "Ingen kilder fundet"
        seed_urls = "  \n".join(
            f"- [{url}]({url})" for url in history.get("seed_urls", [])
        )
        st.markdown(
            f"**{history['name']}**  \n"
            f"Seed-versioner:  \n{seed_urls}  \n"
            f"Indlæst fra: {sources}"
        )
        for warning in history["warnings"]:
            st.warning(f"{history['name']}: {warning}")

st.subheader("Detaljerede ændringer")
if filtered_event_df.empty:
    st.info("Der er ingen detaljerede ændringer at vise for det aktuelle udvalg.")
else:
    st.dataframe(
        filtered_event_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Dato": st.column_config.DateColumn("Dato", format="YYYY-MM-DD"),
            "Lov nr.": st.column_config.NumberColumn("Lov nr.", format="%d"),
            "URL": st.column_config.LinkColumn("URL"),
        },
    )
