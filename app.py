import pandas as pd
import requests
import streamlit as st
from datetime import datetime, timedelta, timezone

st.set_page_config(page_title='SNAI MF League Tracker Light', layout='wide')
st.title('SNAI MF League Tracker')
st.caption('Versione alleggerita per Streamlit Cloud: timeout piu alto, cache, caricamento separato giornata corrente/storico, mercati principali.')

LOCAL_TZ_OFFSET_HOURS = 2
REQUEST_TIMEOUT = 90
SNAI_BASE_URL = 'https://betting-snai.flutterseatech.it/api/vrol-api/vrol'
MF_LEAGUE_DESCRIPTION = 'MF League'
CURRENT_MARKET_IDS = [216, 217, 218, 219]
HISTORY_MARKET_IDS = [218]
MATCHES_PER_BLOCK = 6
TEAM_NAME_MAP = {
    'GEN': 'GEN', 'NAP': 'NAP', 'UDI': 'UDI', 'MIL': 'MIL', 'INT': 'INT', 'ROM': 'ROM',
    'FIO': 'FIO', 'LAZ': 'LAZ', 'SAM': 'SAM', 'ATA': 'ATA', 'VER': 'VER', 'JUV': 'JUV'
}


def normalize_match_name(name):
    name = str(name or '').upper().strip()
    name = name.replace('-', ' ').replace('_', ' ')
    return ' '.join(name.split())


def split_teams(match_name):
    cleaned = normalize_match_name(match_name)
    parts = cleaned.split()
    if len(parts) >= 2:
        return TEAM_NAME_MAP.get(parts[0], parts[0]), TEAM_NAME_MAP.get(parts[-1], parts[-1])
    return cleaned, ''


def implied_probability_from_odds(odds):
    if odds is None or odds <= 1:
        return None
    return 1 / odds


def ranking_bonus(home_rank, away_rank):
    if home_rank is None or away_rank is None:
        return 0.0
    diff = abs(home_rank - away_rank)
    avg_rank = (home_rank + away_rank) / 2
    bonus = 0.0
    if diff <= 2:
        bonus += 0.03
    elif diff <= 5:
        bonus += 0.015
    elif diff >= 12:
        bonus -= 0.03
    if avg_rank <= 6:
        bonus += 0.01
    if avg_rank >= 14:
        bonus += 0.005
    return bonus


def quota_to_decimal(v):
    if v is None:
        return None
    return round(float(v) / 100.0, 2)


@st.cache_data(ttl=120)
def fetch_json(url):
    response = requests.get(
        url,
        timeout=REQUEST_TIMEOUT,
        headers={
            'User-Agent': 'Mozilla/5.0',
            'Accept': 'application/json',
            'Referer': 'https://www.snai.it/'
        }
    )
    response.raise_for_status()
    return response.json()


@st.cache_data(ttl=120)
def fetch_snai_sports():
    return fetch_json(f'{SNAI_BASE_URL}/palinsesto/1/sports')


def get_mf_league_daylist(sports_json):
    for item in sports_json:
        if str(item.get('description', '')).strip() == MF_LEAGUE_DESCRIPTION:
            return item.get('dayList', []) or []
    return []


def select_latest_day(day_list):
    if not day_list:
        return None
    rows = []
    for d in day_list:
        dt = pd.to_datetime(d.get('date'), errors='coerce', utc=True)
        rows.append((dt, d))
    rows = [r for r in rows if pd.notna(r[0])]
    if not rows:
        return day_list[0]
    now = pd.Timestamp.now(tz='UTC')
    past = [d for dt, d in rows if dt <= now]
    if past:
        return sorted(past, key=lambda x: pd.to_datetime(x.get('date'), utc=True), reverse=True)[0]
    return sorted([d for _, d in rows], key=lambda x: pd.to_datetime(x.get('date'), utc=True))[0]


@st.cache_data(ttl=120)
def fetch_championship_detail(sogei_pal_code, day_id):
    return fetch_json(f'{SNAI_BASE_URL}/palinsesto/1/championships/{sogei_pal_code}/{day_id}')


@st.cache_data(ttl=120)
def fetch_championship_markets(sogei_pal_code, day_id, market_ids_tuple):
    all_items = []
    for market_id in list(market_ids_tuple):
        data = fetch_json(f'{SNAI_BASE_URL}/palinsesto/1/championships/{sogei_pal_code}/{day_id}/markets/{market_id}')
        if isinstance(data, list):
            all_items.extend(data)
        elif isinstance(data, dict):
            if isinstance(data.get('marketList'), list):
                all_items.extend(data.get('marketList'))
            else:
                all_items.append(data)
    return all_items


def build_market_map(markets_json):
    market_map = {}
    for item in markets_json:
        event_code = str(item.get('sogeiEventCode', '')).strip()
        market_name = str(item.get('descriptionRef', '')).strip().lower()
        outcomes = item.get('outcomeList', []) or []
        if not event_code:
            continue
        market_map.setdefault(event_code, {})
        parsed = {str(o.get('description')): quota_to_decimal(o.get('quota')) for o in outcomes}
        if market_name == 'esito finale 1x2':
            market_map[event_code]['1'] = parsed.get('1')
            market_map[event_code]['x'] = parsed.get('X')
            market_map[event_code]['2'] = parsed.get('2')
        elif market_name == 'under/over 1,5':
            market_map[event_code]['over_15'] = parsed.get('Over')
            market_map[event_code]['under_15'] = parsed.get('Under')
        elif market_name == 'under/over 2,5':
            market_map[event_code]['over_25'] = parsed.get('Over')
            market_map[event_code]['under_25'] = parsed.get('Under')
        elif market_name == 'under/over 3,5':
            market_map[event_code]['over_35'] = parsed.get('Over')
            market_map[event_code]['under_35'] = parsed.get('Under')
    return market_map


def build_current_input_df(detail_json, markets_json):
    description_map = detail_json.get('sogeiEventCodeDescriptionMap', {}) or {}
    index_map = detail_json.get('sogeiEventCodeIndexMap', {}) or {}
    market_map = build_market_map(markets_json)
    rows = []
    for event_code, match_name in description_map.items():
        event_code = str(event_code)
        match_name = normalize_match_name(match_name)
        home_team, away_team = split_teams(match_name)
        mm = market_map.get(event_code, {})
        rows.append({
            'match': match_name,
            'home_team': home_team,
            'away_team': away_team,
            'event_code': event_code,
            'match_index': index_map.get(event_code),
            'quota_1': mm.get('1'),
            'quota_x': mm.get('x'),
            'quota_2': mm.get('2'),
            'quota_over_15': mm.get('over_15'),
            'quota_over_25': mm.get('over_25'),
            'quota_over_35': mm.get('over_35'),
            'quota_gg': mm.get('over_25'),
            'rank_home': None,
            'rank_away': None,
        })
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out.sort_values(['match_index', 'event_code'], ascending=[True, True], kind='stable').reset_index(drop=True)


def infer_esito_from_over25(quota_over_25):
    if quota_over_25 is None:
        return 'ND'
    return 'GOL' if quota_over_25 <= 1.85 else 'NO GOL'


def ensure_block_columns(df):
    if df is None or df.empty:
        return pd.DataFrame()
    work = df.copy()
    work['timestamp'] = pd.to_datetime(work['timestamp'], errors='coerce', utc=True)
    work['sort_timestamp'] = work['timestamp']
    work['giornata'] = pd.to_numeric(work['giornata'], errors='coerce')
    work = work.dropna(subset=['giornata']).copy()
    if work.empty:
        return work
    work['giornata'] = work['giornata'].astype(int)
    work = work.sort_values(['sort_timestamp', 'giornata', 'codice_avvenimento'], kind='stable').reset_index(drop=True)
    cycle_ids = []
    current_cycle = 1
    prev_g = None
    for g in work['giornata'].tolist():
        if prev_g is not None and g <= prev_g:
            current_cycle += 1
        cycle_ids.append(current_cycle)
        prev_g = g
    work['cycle_id'] = cycle_ids
    work['group_key'] = work['cycle_id'].astype(str) + '-' + work['giornata'].astype(str)
    work['group_label'] = work.apply(lambda r: f"Ciclo {int(r['cycle_id'])} · Giornata {int(r['giornata'])}", axis=1)
    work['match_nel_blocco'] = work.groupby('group_key', sort=False).cumcount() + 1
    return work.reset_index(drop=True)


def get_valid_matches_df(df):
    if df is None or df.empty or 'esito' not in df.columns:
        return pd.DataFrame()
    return df[df['esito'].isin(['GOL', 'NO GOL'])].copy()


def build_blocks(df):
    valid_df = ensure_block_columns(get_valid_matches_df(df))
    if valid_df.empty:
        return pd.DataFrame(columns=['cycle_id', 'giornata', 'partite', 'GG', 'NOGOL', 'sul_totale', 'group_label'])
    grouped = valid_df.groupby(['group_key', 'cycle_id', 'giornata', 'group_label'], dropna=False).agg(
        partite=('esito', 'count'),
        GG=('esito', lambda x: int((x == 'GOL').sum())),
        NOGOL=('esito', lambda x: int((x == 'NO GOL').sum())),
        last_ts=('sort_timestamp', 'max')
    ).reset_index()
    grouped['sul_totale'] = (grouped['GG'] / grouped['partite'] * 100).round(2)
    grouped = grouped.sort_values(['last_ts', 'cycle_id', 'giornata'], ascending=[False, False, False], kind='stable')
    return grouped[['cycle_id', 'giornata', 'partite', 'GG', 'NOGOL', 'sul_totale', 'group_label']].reset_index(drop=True)


def team_recent_form(df, team_code, max_matchdays=10):
    valid_df = ensure_block_columns(get_valid_matches_df(df))
    if valid_df.empty:
        return pd.DataFrame(columns=['group_label', 'esito'])
    subset = valid_df[(valid_df['home_team'] == team_code) | (valid_df['away_team'] == team_code)].copy()
    if subset.empty:
        return pd.DataFrame(columns=['group_label', 'esito'])
    group_order = (
        subset.groupby('group_key', dropna=False)
        .agg(last_ts=('sort_timestamp', 'max'), group_label=('group_label', 'first'))
        .reset_index(drop=True)
        .sort_values('last_ts', ascending=False, kind='stable')
    )
    keep_labels = group_order.head(max_matchdays)['group_label'].tolist()
    subset = subset[subset['group_label'].isin(keep_labels)].copy()
    subset = subset.drop_duplicates(subset=['group_label', 'home_team', 'away_team', 'esito'])
    return subset[['group_label', 'esito']]


def rate_from_last_matchdays(team_df, n_days):
    if team_df.empty:
        return 0.0, 0
    ordered_days = []
    for g in team_df['group_label'].dropna().tolist():
        if g not in ordered_days:
            ordered_days.append(g)
    keep_days = ordered_days[:n_days]
    subset = team_df[team_df['group_label'].isin(keep_days)].copy()
    matches = len(subset)
    if matches == 0:
        return 0.0, 0
    gg_rate = float((subset['esito'] == 'GOL').sum() / matches)
    return gg_rate, matches


def get_team_trend_5_10(df, team_code):
    team_df = team_recent_form(df, team_code, max_matchdays=10)
    rate_5, matches_5 = rate_from_last_matchdays(team_df, 5)
    rate_10, matches_10 = rate_from_last_matchdays(team_df, 10)
    trend_score = (0.6 * rate_5) + (0.4 * rate_10)
    return {'rate_5': rate_5, 'rate_10': rate_10, 'trend_score': trend_score, 'matches_5': matches_5, 'matches_10': matches_10}


def get_global_trend_score(df):
    blocks = build_blocks(df)
    if blocks.empty:
        return {'rate_5': 0.0, 'rate_10': 0.0, 'trend_score': 0.0}
    blocks = blocks.copy()
    blocks['rate'] = blocks['GG'] / blocks['partite']
    def mean_rate(n):
        subset = blocks.head(n)
        return float(subset['rate'].mean()) if not subset.empty else 0.0
    rate_5 = mean_rate(5)
    rate_10 = mean_rate(10)
    trend_score = (0.6 * rate_5) + (0.4 * rate_10)
    return {'rate_5': rate_5, 'rate_10': rate_10, 'trend_score': trend_score}


def build_match_scores(input_df, history_df):
    if input_df.empty:
        return pd.DataFrame()
    global_trend = get_global_trend_score(history_df)
    rows = []
    for _, r in input_df.iterrows():
        home_trend = get_team_trend_5_10(history_df, r['home_team'])
        away_trend = get_team_trend_5_10(history_df, r['away_team'])
        prob_mercato = implied_probability_from_odds(r['quota_gg']) or 0.0
        team_trend_avg = (home_trend['trend_score'] + away_trend['trend_score']) / 2
        bonus_classifica = ranking_bonus(r.get('rank_home'), r.get('rank_away'))
        score_finale = (0.50 * team_trend_avg) + (0.25 * global_trend['trend_score']) + (0.20 * prob_mercato) + (0.05 * bonus_classifica)
        score_finale = max(0.0, min(0.95, score_finale))
        rows.append({
            'match': r['match'],
            'home_team': r['home_team'],
            'away_team': r['away_team'],
            'quota_gg': r['quota_gg'],
            'prob_mercato': prob_mercato,
            'home_rate_5': home_trend['rate_5'],
            'home_rate_10': home_trend['rate_10'],
            'away_rate_5': away_trend['rate_5'],
            'away_rate_10': away_trend['rate_10'],
            'team_trend_avg': team_trend_avg,
            'global_trend': global_trend['trend_score'],
            'bonus_classifica': bonus_classifica,
            'score_finale': score_finale,
        })
    return pd.DataFrame(rows).sort_values('score_finale', ascending=False).reset_index(drop=True)


def assign_topn_predictions(score_df):
    if score_df.empty:
        return score_df.copy(), 0.0, 0
    out = score_df.copy()
    expected_gg_total = float(out['score_finale'].sum())
    gg_slots = max(0, min(len(out), int(round(expected_gg_total))))
    out['prediction'] = 'NG'
    if gg_slots > 0:
        out.loc[:gg_slots - 1, 'prediction'] = 'GG'
    return out, round(expected_gg_total, 2), gg_slots


def build_history_from_days(day_list, max_days=3):
    rows = []
    if not day_list:
        return pd.DataFrame()
    sorted_days = sorted(day_list, key=lambda x: pd.to_datetime(x.get('date'), utc=True), reverse=False)[-max_days:]
    for d in sorted_days:
        sogei_pal_code = str(d.get('sogeiPalCode'))
        day_id = d.get('dayId')
        day_code = d.get('dayCode')
        dt = pd.to_datetime(d.get('date'), utc=True, errors='coerce')
        try:
            detail = fetch_championship_detail(sogei_pal_code, day_id)
            markets = fetch_championship_markets(sogei_pal_code, day_id, tuple(HISTORY_MARKET_IDS))
            description_map = detail.get('sogeiEventCodeDescriptionMap', {}) or {}
            market_map = build_market_map(markets)
            for event_code, match_name in description_map.items():
                event_code = str(event_code)
                match_name = normalize_match_name(match_name)
                home_team, away_team = split_teams(match_name)
                mm = market_map.get(event_code, {})
                rows.append({
                    'timestamp': dt,
                    'giornata': int(day_code) if str(day_code).isdigit() else None,
                    'codice_palinsesto': sogei_pal_code,
                    'codice_avvenimento': event_code,
                    'descrizione_avventimento': match_name,
                    'home_team': home_team,
                    'away_team': away_team,
                    'esito': infer_esito_from_over25(mm.get('over_25'))
                })
        except Exception:
            continue
    hist_df = pd.DataFrame(rows)
    if hist_df.empty:
        return hist_df
    return ensure_block_columns(hist_df)


if 'sports_json' not in st.session_state:
    st.session_state['sports_json'] = None
if 'day_list' not in st.session_state:
    st.session_state['day_list'] = []
if 'current_day' not in st.session_state:
    st.session_state['current_day'] = None
if 'current_input_df' not in st.session_state:
    st.session_state['current_input_df'] = pd.DataFrame()
if 'history_df' not in st.session_state:
    st.session_state['history_df'] = pd.DataFrame()

st.subheader('Controlli')
c1, c2 = st.columns(2)
with c1:
    if st.button('Carica giornata corrente', type='primary'):
        try:
            sports_json = fetch_snai_sports()
            day_list = get_mf_league_daylist(sports_json)
            current_day = select_latest_day(day_list)
            st.session_state['sports_json'] = sports_json
            st.session_state['day_list'] = day_list
            st.session_state['current_day'] = current_day
            if current_day:
                detail_json = fetch_championship_detail(current_day['sogeiPalCode'], current_day['dayId'])
                markets_json = fetch_championship_markets(current_day['sogeiPalCode'], current_day['dayId'], tuple(CURRENT_MARKET_IDS))
                st.session_state['current_input_df'] = build_current_input_df(detail_json, markets_json)
            st.success('Giornata corrente caricata.')
        except Exception as e:
            st.error(f'Errore giornata corrente: {e}')
with c2:
    if st.button('Carica storico leggero'):
        try:
            if not st.session_state['day_list']:
                sports_json = fetch_snai_sports()
                st.session_state['sports_json'] = sports_json
                st.session_state['day_list'] = get_mf_league_daylist(sports_json)
            st.session_state['history_df'] = build_history_from_days(st.session_state['day_list'], max_days=3)
            st.success('Storico leggero caricato.')
        except Exception as e:
            st.error(f'Errore storico: {e}')

current_day = st.session_state['current_day']
current_input_df = st.session_state['current_input_df']
history_df = st.session_state['history_df']

if current_day:
    m1, m2, m3, m4 = st.columns(4)
    m1.metric('Palinsesto', current_day.get('sogeiPalCode', '-'))
    m2.metric('Day ID', current_day.get('dayId', '-'))
    m3.metric('Giornata', current_day.get('dayCode', '-'))
    m4.metric('Competition', current_day.get('competitionDescription', '-'))

if not current_input_df.empty:
    st.subheader('Palinsesto corrente')
    st.dataframe(current_input_df, use_container_width=True, hide_index=True)
else:
    st.info('Carica la giornata corrente per vedere le partite e le quote.')

if not history_df.empty:
    st.subheader('Storico leggero')
    blocks_df = build_blocks(history_df)
    h1, h2, h3 = st.columns(3)
    h1.metric('Partite storico', int(len(history_df)))
    h2.metric('Blocchi', int(len(blocks_df)))
    h3.metric('GG stimati', int((history_df['esito'] == 'GOL').sum()))
    st.dataframe(blocks_df, use_container_width=True, hide_index=True)
else:
    st.info('Carica lo storico leggero per alimentare la predict.')

if not current_input_df.empty and not history_df.empty:
    st.subheader('Predict corrente Top-N')
    score_df = build_match_scores(current_input_df[['match', 'home_team', 'away_team', 'quota_gg', 'rank_home', 'rank_away']], history_df)
    pred_df, expected_gg_total, gg_slots = assign_topn_predictions(score_df)
    p1, p2, p3 = st.columns(3)
    p1.metric('GG attesi', expected_gg_total)
    p2.metric('Slot GG', gg_slots)
    p3.metric('Partite', int(len(pred_df)))
    st.dataframe(pred_df, use_container_width=True, hide_index=True)
