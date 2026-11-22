"""Staff analytics (GET /analytics/*). Every list is ONE series ranked by size → horizontal
bars in a single validated hue (palette slot 1, passes light + dark surfaces), sorted, with a
hover tooltip and a table view; the headline numbers are stat tiles, not charts."""

import altair as alt
import pandas as pd
import streamlit as st

import api_client as api
from components import call, money

SERIES = '#2a78d6'          # dataviz reference palette, categorical slot 1 (validated light + dark)

st.title('📊 Analytics')
f1, f2 = st.columns([3, 1])
days = f1.select_slider('Period', [7, 14, 30, 90, 180, 365], value=30, format_func=lambda d: f'last {d} days')
limit = f2.number_input('Items per list', 3, 50, 10)


def ranked(rows: list[dict], what: str, key: str, *, share: bool = False) -> None:
    """Horizontal bars, biggest first; table view in a toggle (never colour-only information)."""
    if not rows:
        st.caption(f'No {what.lower()} in this period.')
        return
    df = pd.DataFrame(rows).rename(columns={'name': what, 'count': 'Count'})
    tooltip = [alt.Tooltip(f'{what}:N'), alt.Tooltip('Count:Q')]
    if share:
        df['Share'] = df['share']
        tooltip.append(alt.Tooltip('Share:Q', format='.0%'))
    chart = (alt.Chart(df)
             .mark_bar(color=SERIES, cornerRadiusEnd=4, size=14)
             .encode(x=alt.X('Count:Q', axis=alt.Axis(tickMinStep=1, grid=True, gridOpacity=0.25, title=None)),
                     y=alt.Y(f'{what}:N', sort='-x', axis=alt.Axis(title=None, labelLimit=220)),
                     tooltip=tooltip)
             .properties(height=max(60, 26 * len(df)))
             .configure_view(stroke=None))
    st.altair_chart(chart, width='stretch')
    if st.toggle('Show as table', key=f'table-{key}'):
        st.dataframe(df[[what, 'Count'] + (['Share'] if share else [])], hide_index=True)


demand_tab, unmet_tab, reservations_tab, intents_tab = st.tabs(
    ['Demand', 'Unmet demand', 'Reservations', 'Requests by intent'])

with demand_tab:
    d = call(api.analytics, 'demand', days, limit)
    if d:
        st.metric('Searches through the assistant', d['searches'])
        left, right = st.columns(2)
        with left:
            st.subheader('Genres')
            ranked(d['genres'], 'Genre', 'demand-genres')
            st.subheader('Decades')
            ranked(d['decades'], 'Decade', 'demand-decades')
        with right:
            st.subheader('Artists')
            ranked(d['artists'], 'Artist', 'demand-artists')
            st.subheader('Titles')
            ranked(d['titles'], 'Title', 'demand-titles')

with unmet_tab:
    u = call(api.analytics, 'unmet-demand', days, limit)
    if u:
        a, b, c = st.columns(3)
        a.metric('Searches', u['searches'])
        b.metric('Found nothing', u['zero_result_searches'], help='Even after the assistant widened the search.')
        c.metric('Share with no result', f'{u["zero_result_rate"]:.0%}' if u['zero_result_rate'] is not None else '—')
        left, right = st.columns(2)
        with left:
            st.subheader('Wanted, not in stock')
            ranked(u['titles'], 'Title', 'unmet-titles')
            ranked(u['artists'], 'Artist', 'unmet-artists')
        with right:
            st.subheader('Most wanted (open wantlists)')
            if u['most_wanted']:
                st.dataframe(pd.DataFrame([{'Artist': w['artist'], 'Title': w['title'] or '(anything)',
                                            'Customers': w['customers'],
                                            'Would pay up to': money(w['highest_max_price'])}
                                           for w in u['most_wanted']]), hide_index=True)
            else:
                st.caption('No open wantlist entries.')
        st.subheader('Latest searches that found nothing')
        for s in u['recent']:
            st.caption(f'{s["at"][:16].replace("T", " ")} · ' + ', '.join(f'{k}: {v}' for k, v in s['asked'].items()))

with reservations_tab:
    r = call(api.analytics, 'reservations', days)
    if r:
        cols = st.columns(5)
        for col, key in zip(cols, ['created', 'active', 'completed', 'cancelled', 'expired']):
            col.metric(key.capitalize(), r[key])
        a, b = st.columns(2)
        a.metric('Conversion (completed / created)',
                 f'{r["conversion_rate"]:.0%}' if r['conversion_rate'] is not None else '—')
        b.metric('Of the closed ones, completed',
                 f'{r["completion_rate_of_closed"]:.0%}' if r['completion_rate_of_closed'] is not None else '—',
                 help='Ignores reservations that are still active.')

with intents_tab:
    i = call(api.analytics, 'intents', days)
    if i:
        st.metric('Customer requests', i['total'])
        ranked(i['intents'], 'Intent', 'intents', share=True)
