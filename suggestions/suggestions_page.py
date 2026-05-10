import html
import json


def _e(v):
    return html.escape(str(v if v is not None else ""))


def _sev(v):
    v = str(v or "healthy").lower()
    if v not in ("healthy", "warning", "critical"):
        return "healthy"
    return v


def _rows(items, columns, empty_text):
    if not items:
        return f"<tr><td colspan='{len(columns)}'>{html.escape(empty_text)}</td></tr>"

    out = []
    for item in items:
        sev = _sev(item.get("severity", "healthy"))
        cells = []
        for key, label in columns:
            val = item.get(key, "")
            if isinstance(val, (list, tuple)):
                val = ", ".join(str(x) for x in val)
            cells.append(f"<td>{_e(val)}</td>")
        out.append(f"<tr class='{sev}'>{''.join(cells)}</tr>")
    return "".join(out)


def render_suggestions_html(data):
    data = data or {}

    changes = data.get("changes", []) or []
    reasons = data.get("reasons") or []
    score = data.get("score") or {}
    quality = data.get("quality") or {}
    summary_cards = data.get("summary_cards") or []
    site_groups = data.get("site_groups") or []
    site_reports = data.get("site_reports") or []
    script_reports = data.get("script_reports") or []
    script_summary = data.get("script_summary") or {}
    feature_reports = data.get("feature_reports") or []

    front_cards = data.get("front_cards") or []
    front_signals = data.get("front_signals") or []
    front_recommendations = data.get("front_recommendations") or []
    front_knobs = data.get("front_knobs") or []
    front_note = data.get("front_note", "-")
    front_health_level = _sev(data.get("front_health_level", "healthy"))

    if front_cards:
        front_cards_html = "".join(
            f"""
            <div class="metric {_sev(x.get('severity'))}">
              <div class="metricTitle">{_e(x.get('title'))}</div>
              <div class="metricValue">{_e(x.get('value'))}</div>
              <div class="metricDetail">{_e(x.get('detail'))}</div>
            </div>
            """
            for x in front_cards
        )
    else:
        front_cards_html = """
        <div class="metric healthy">
          <div class="metricTitle">Front IP / SNI</div>
          <div class="metricValue">-</div>
          <div class="metricDetail">No front health data available.</div>
        </div>
        """

    front_signal_rows = _rows(
        front_signals,
        [
            ("name", "Signal"),
            ("value", "Value"),
            ("detail", "Detail"),
        ],
        "No Front IP / SNI signals yet.",
    )

    front_rec_rows = _rows(
        front_recommendations,
        [
            ("title", "Title"),
            ("issue", "Issue"),
            ("recommendation", "Recommendation"),
            ("command", "Command"),
        ],
        "No Front IP / SNI recommendations.",
    )

    front_knob_rows = _rows(
        front_knobs,
        [
            ("key", "Key"),
            ("value", "Value"),
            ("recommended", "Recommended"),
        ],
        "No Front IP / SNI knobs yet.",
    )


    runtime_knobs = data.get("runtime_knobs") or []

    server_cards = data.get("server_cards") or []
    server_signals = data.get("server_signals") or []
    server_recommendations = data.get("server_recommendations") or []
    server_knobs = data.get("server_knobs") or []
    server_health_level = _sev(data.get("server_health_level", "healthy"))
    server_note = _e(data.get("server_note", ""))

    note = _e(data.get("note", ""))
    error_rate = _e(data.get("error_rate", "-"))
    next_refresh = _e(data.get("next_refresh_in", "-"))
    health_level = _sev(data.get("health_level", "healthy"))
    request_sample = _e(data.get("request_sample", "-"))
    analysis_version = _e(data.get("analysis_version", "advanced"))

    reasons_html = "".join(f"<li>{_e(x)}</li>" for x in reasons) or "<li>No issues detected.</li>"

    score_items = "".join(
        f"<span class='pill'>{_e(k)}: <b>{_e(v)}</b></span>"
        for k, v in score.items()
    ) or "<span class='pill'>No log signals yet</span>"

    quality_items = "".join(
        f"<span class='quality'><b>{_e(k).title()}</b><em>{_e(v)}/100</em></span>"
        for k, v in quality.items()
    ) or "<span class='quality'><b>Quality</b><em>-</em></span>"

    if summary_cards:
        cards_html = "".join(
            f"""
            <div class="metric {_sev(x.get('severity'))}">
              <div class="metricTitle">{_e(x.get('title'))}</div>
              <div class="metricValue">{_e(x.get('value'))}</div>
              <div class="metricDetail">{_e(x.get('detail'))}</div>
            </div>
            """
            for x in summary_cards
        )
    else:
        cards_html = """
        <div class="metric healthy">
          <div class="metricTitle">Status</div>
          <div class="metricValue">-</div>
          <div class="metricDetail">No summary available.</div>
        </div>
        """

    if server_cards:
        server_cards_html = "".join(
            f"""
            <div class="metric {_sev(x.get('severity'))}">
              <div class="metricTitle">{_e(x.get('title'))}</div>
              <div class="metricValue">{_e(x.get('value'))}</div>
              <div class="metricDetail">{_e(x.get('detail'))}</div>
            </div>
            """
            for x in server_cards
        )
    else:
        server_cards_html = """
        <div class="metric warning">
          <div class="metricTitle">Exit Node</div>
          <div class="metricValue">No Data</div>
          <div class="metricDetail">No server health snapshot is available yet.</div>
        </div>
        """

    server_signal_rows = _rows(
        server_signals,
        [
            ("name", "Signal"),
            ("value", "Value"),
            ("detail", "Detail"),
        ],
        "No server signals yet.",
    )

    server_recommendation_rows = _rows(
        server_recommendations,
        [
            ("title", "Recommendation"),
            ("issue", "Issue"),
            ("recommendation", "Action"),
            ("command", "Suggested env / command"),
        ],
        "No server-specific recommendations.",
    )

    server_knob_rows = _rows(
        server_knobs,
        [
            ("key", "Metric / Env"),
            ("value", "Current"),
            ("recommended", "Recommendation"),
        ],
        "No server knobs yet.",
    )

    change_rows = []
    for item in changes:
        change_rows.append(
            f"<tr class='change'>"
            f"<td>{_e(item.get('area', 'General'))}</td>"
            f"<td><code>{_e(item.get('key', ''))}</code></td>"
            f"<td>{_e(item.get('old', ''))}</td>"
            f"<td>{_e(item.get('new', ''))}</td>"
            f"<td>{_e(item.get('impact', ''))}</td>"
            f"<td>{_e(item.get('why', ''))}</td>"
            f"</tr>"
        )

    if not change_rows:
        change_rows.append('<tr><td colspan="6">No changes suggested.</td></tr>')

    site_group_rows = _rows(
        site_groups,
        [
            ("title", "Area"),
            ("requests", "Requests"),
            ("errors", "Errors"),
            ("error_rate", "Error %"),
            ("bytes_h", "Traffic"),
            ("hosts", "Hosts"),
        ],
        "No site groups yet.",
    )

    site_rows = _rows(
        site_reports,
        [
            ("host", "Host"),
            ("category", "Category"),
            ("requests", "Req"),
            ("errors", "Err"),
            ("error_rate", "Err %"),
            ("bytes_h", "Traffic"),
            ("issue", "Issue"),
            ("recommendation", "Recommendation"),
        ],
        "No host-level data yet.",
    )

    script_rows = _rows(
        script_reports,
        [
            ("script", "Script"),
            ("requests", "Req"),
            ("errors", "Err"),
            ("error_rate", "Err %"),
            ("quota_percent", "Quota %"),
            ("last_status", "Last"),
            ("issue", "Issue"),
        ],
        "No script data yet.",
    )

    feature_rows = _rows(
        [
            {
                "name": x.get("name"),
                "enabled": "ON" if x.get("enabled") else "OFF",
                "global_off": "YES" if x.get("global_off") else "NO",
                "detail": x.get("detail", ""),
                "severity": "healthy" if x.get("enabled") else "warning" if x.get("global_off") else "healthy",
            }
            for x in feature_reports
        ],
        [
            ("name", "Feature"),
            ("enabled", "State"),
            ("global_off", "Global off"),
            ("detail", "Details"),
        ],
        "No feature data yet.",
    )

    runtime_knob_rows = _rows(
        [
            {
                "key": x.get("key"),
                "value": x.get("value"),
                "severity": x.get("severity", "healthy"),
            }
            for x in runtime_knobs
        ],
        [
            ("key", "Key"),
            ("value", "Value"),
        ],
        "No runtime knobs yet.",
    )

    can_apply = bool(data.get("can_apply"))
    apply_disabled = "" if can_apply else "disabled"
    apply_title = "Apply these suggestions to Auto mode" if can_apply else "No changes to apply"

    safe_mode = bool(data.get("safe_mode_active"))
    safe_mode_banner = ""
    if safe_mode:
        safe_mode_banner = """
        <div class="safeBanner">
          Safe Mode is active: H2, Turbo, SABR, Video Prefetch, Manifest Prefetch and Video Passthrough are all off.
          Suggestions will not re-enable dashboard-disabled features automatically.
        </div>
        """

    raw_json = html.escape(json.dumps(data, ensure_ascii=False, indent=2))

    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>MHR Suggestions / Advisor</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{{
  --bg:#050812;
  --card:#0b1220dd;
  --card2:#101827dd;
  --line:#ffffff16;
  --text:#edf5ff;
  --muted:#8fa3bd;
  --green:#34d399;
  --yellow:#fbbf24;
  --red:#fb7185;
  --cyan:#22d3ee;
  --purple:#a78bfa;
}}
*{{box-sizing:border-box}}
body{{
  margin:0;
  min-height:100vh;
  background:
    radial-gradient(circle at 10% 0%,#2563eb35,transparent 30%),
    radial-gradient(circle at 90% 0%,#7c3aed35,transparent 30%),
    linear-gradient(180deg,#08111f,var(--bg));
  color:var(--text);
  font-family:Segoe UI,Inter,Arial,sans-serif;
}}
main{{max-width:1500px;margin:0 auto;padding:18px}}
h1{{margin:0;font-size:25px;letter-spacing:-.03em}}
h2{{margin:0 0 12px;font-size:17px}}
.muted{{color:var(--muted);line-height:1.55}}
.card{{
  background:linear-gradient(180deg,var(--card),var(--card2));
  border:1px solid var(--line);
  border-radius:22px;
  padding:16px;
  margin:14px 0;
  box-shadow:0 24px 70px #0008;
  overflow:hidden;
}}
.top{{
  display:grid;
  grid-template-columns:1fr auto;
  gap:12px;
  align-items:start;
}}
.status{{
  display:inline-flex;
  align-items:center;
  gap:8px;
  border-radius:999px;
  padding:8px 11px;
  font-weight:900;
  border:1px solid var(--line);
}}
.status.healthy{{color:#bbf7d0;background:#052e1a99;border-color:#166534}}
.status.warning{{color:#fde68a;background:#211505cc;border-color:#854d0e}}
.status.critical{{color:#fecaca;background:#2a0710cc;border-color:#7f1d1d}}
.actions{{display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end}}
button{{
  background:linear-gradient(135deg,#2563eb,#7c3aed);
  color:white;
  border:0;
  border-radius:999px;
  padding:11px 16px;
  font-weight:900;
  cursor:pointer;
}}
button.secondary{{background:#111827;border:1px solid #ffffff20}}
button:disabled{{opacity:.45;cursor:not-allowed;background:#374151}}
.grid{{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:12px}}
.metric{{
  border:1px solid var(--line);
  border-radius:18px;
  padding:13px;
  background:#03071280;
}}
.metricTitle{{color:var(--muted);font-size:12px;font-weight:900;text-transform:uppercase;letter-spacing:.08em}}
.metricValue{{font-size:25px;font-weight:980;margin-top:8px}}
.metricDetail{{color:var(--muted);font-size:12px;line-height:1.45;margin-top:8px}}
.metric.healthy .metricValue{{color:var(--green)}}
.metric.warning .metricValue{{color:var(--yellow)}}
.metric.critical .metricValue{{color:var(--red)}}
.qualityWrap{{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}}
.quality,.pill{{
  display:inline-flex;
  gap:8px;
  align-items:center;
  padding:8px 10px;
  border-radius:999px;
  border:1px solid #ffffff18;
  background:#ffffff08;
  margin:4px 4px 4px 0;
}}
.quality em{{font-style:normal;color:var(--cyan);font-weight:900}}

.analysisNotice{{
  margin-top:12px;
  border-radius:16px;
  padding:12px 13px;
  background:#06202acc;
  color:#dffaff;
  border:1px solid #155e75;
  line-height:1.55;
  font-weight:800;
}}
.analysisNotice .muted{{
  margin-top:6px;
  font-weight:700;
}}
.analysisNotice b{{
  color:#a5f3fc;
}}

.safeBanner{{
  margin-top:12px;
  border-radius:16px;
  padding:12px 13px;
  background:#211505cc;
  color:#fde68a;
  border:1px solid #854d0e;
  line-height:1.5;
  font-weight:800;
}}
table{{width:100%;border-collapse:separate;border-spacing:0;font-size:13px}}
th,td{{padding:10px;border-bottom:1px solid #ffffff10;text-align:left;vertical-align:top}}
th{{color:#93a4bb;background:#ffffff08;font-weight:900;position:sticky;top:0}}
tr.healthy td{{color:#d7fbe8}}
tr.warning td{{color:#fde68a}}
tr.critical td{{color:#fecaca}}
tr.change td{{color:#e8eef8}}
code{{background:#020617;padding:3px 6px;border-radius:7px;color:#a5f3fc}}
.tableWrap{{max-height:430px;overflow:auto;border:1px solid #ffffff10;border-radius:16px;background:#03071266}}
.cols{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}
ul{{line-height:1.65}}
details summary{{cursor:pointer;color:#a5f3fc;font-weight:900}}
pre{{
  white-space:pre-wrap;
  word-break:break-word;
  background:#020617;
  border:1px solid #ffffff12;
  border-radius:16px;
  padding:12px;
  max-height:420px;
  overflow:auto;
  color:#cbd5e1;
}}
@media(max-width:1100px){{
  .grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}
  .cols{{grid-template-columns:1fr}}
  .top{{grid-template-columns:1fr}}
  .actions{{justify-content:flex-start}}
}}
@media(max-width:620px){{
  main{{padding:10px}}
  .grid{{grid-template-columns:1fr}}
}}

.serverHeader{{
  display:flex;
  justify-content:space-between;
  align-items:flex-start;
  gap:12px;
  flex-wrap:wrap;
}}
.serverHeader .serverTitle{{
  display:flex;
  align-items:center;
  gap:10px;
}}
.serverDot{{
  width:10px;
  height:10px;
  border-radius:50%;
  background:var(--green);
  box-shadow:0 0 14px var(--green);
}}
.serverDot.warning{{
  background:var(--yellow);
  box-shadow:0 0 14px var(--yellow);
}}
.serverDot.critical{{
  background:var(--red);
  box-shadow:0 0 14px var(--red);
}}
.serverNote{{
  margin-top:8px;
  color:var(--muted);
  line-height:1.55;
}}
.serverSplit{{
  display:grid;
  grid-template-columns:1.1fr .9fr;
  gap:14px;
}}
@media(max-width:1100px){{
  .serverSplit{{grid-template-columns:1fr}}
}}











/* PATCH_SUGGESTIONS_FINAL_I18N_CSS_START */
:root{{
  --mhr-font-ui: Inter, "Segoe UI", Roboto, Arial, sans-serif;
  --mhr-font-fa: Vazirmatn, Vazir, IRANSans, "Segoe UI", Tahoma, Arial, sans-serif;
  --mhr-font-mono: "Cascadia Mono", Consolas, monospace;
}}

html[lang="fa"],
html[lang="fa"] body,
body.rtl{{
  direction:rtl;
  text-align:right;
  font-family:var(--mhr-font-fa) !important;
}}

html[lang="en"],
html[lang="en"] body,
body.ltr{{
  direction:ltr;
  text-align:left;
  font-family:var(--mhr-font-ui) !important;
}}

html[lang="fa"] *,
html[lang="fa"] button,
html[lang="fa"] input,
html[lang="fa"] select,
html[lang="fa"] textarea,
html[lang="fa"] .actions button,
html[lang="fa"] .langMini,
html[lang="fa"] .status,
html[lang="fa"] .metric,
html[lang="fa"] .metricTitle,
html[lang="fa"] .metricValue,
html[lang="fa"] .metricDetail,
html[lang="fa"] .quality,
html[lang="fa"] .pill,
html[lang="fa"] table,
html[lang="fa"] th,
html[lang="fa"] td,
html[lang="fa"] h1,
html[lang="fa"] h2,
html[lang="fa"] .muted{{
  font-family:var(--mhr-font-fa) !important;
  letter-spacing:0 !important;
}}

html[lang="en"] button,
html[lang="en"] .actions button,
html[lang="en"] .langMini,
html[lang="en"] .status{{
  font-family:var(--mhr-font-ui) !important;
}}

html[lang="fa"] .top,
html[lang="fa"] .actions,
html[lang="fa"] .serverHeader,
html[lang="fa"] .serverHeader .serverTitle,
html[lang="fa"] .qualityWrap,
html[lang="fa"] .grid,
html[lang="fa"] .cols,
html[lang="fa"] .serverSplit{{
  direction:rtl;
}}

html[lang="en"] .top,
html[lang="en"] .actions,
html[lang="en"] .serverHeader,
html[lang="en"] .serverHeader .serverTitle,
html[lang="en"] .qualityWrap,
html[lang="en"] .grid,
html[lang="en"] .cols,
html[lang="en"] .serverSplit{{
  direction:ltr;
}}

html[lang="fa"] .actions{{
  justify-content:flex-start;
}}

html[lang="en"] .actions{{
  justify-content:flex-end;
}}

html[lang="fa"] th,
html[lang="fa"] td{{
  text-align:right;
}}

html[lang="en"] th,
html[lang="en"] td{{
  text-align:left;
}}

html[lang="fa"] pre,
html[lang="fa"] code{{
  direction:ltr;
  text-align:left;
  font-family:var(--mhr-font-mono) !important;
}}

.langMini{{
  background:#111827 !important;
  border:1px solid #ffffff20 !important;
  color:white !important;
  border-radius:999px !important;
  padding:11px 14px !important;
  font-weight:900 !important;
  cursor:pointer !important;
}}
/* PATCH_SUGGESTIONS_FINAL_I18N_CSS_END */

</style>
</head>
<body>
<main>
  <div class="card">
    <div class="top">
      <div>
        <h1>MHR Suggestions / Advisor</h1>
        <div class="muted">
          Multi-layer analysis for video, web browsing, relay pressure, Apps Script health, load failures and feature states.
          Version: {analysis_version}
        </div>
      </div>
      <div class="actions">
        <span class="status {health_level}">{health_level.upper()}</span>
        <button onclick="applySuggestion()" {apply_disabled} title="{apply_title}">Apply to Auto mode</button>
        <button class="secondary" onclick="safeReload()">Refresh</button>
        <button class="secondary langMini" type="button" id="mhrLangBtn">FA</button>
      </div>
    </div>

    <p>Status: <b>{note}</b></p>
    <div class="analysisNotice">
      <div><b>Analyzing data...</b> Auto refresh in <b><span id="refreshCountdown">{next_refresh}</span>s</b>.</div>
      <div class="muted">Tip: keep browsing, watching videos, downloading, and doing your normal work so MHR can collect enough traffic data and analyze it for you :)</div>
      <div class="muted">Error rate: <b>{error_rate}%</b> | Requests: <b>{request_sample}</b></div>
    </div>
    {safe_mode_banner}
    <div class="qualityWrap">{quality_items}</div>
  </div>

  <div class="grid">
    {cards_html}
  </div>

  <div class="card">
    <div class="serverHeader">
      <div>
        <div class="serverTitle">
          <span class="serverDot {server_health_level}"></span>
          <h2>Exit Node / Server Health</h2>
        </div>
        <div class="serverNote">{server_note}</div>
        <div class="muted">
          This section uses only the latest server health snapshot already collected by main.py.
          It does not send a new request to the exit node.
        </div>
      </div>
      <span class="status {server_health_level}">SERVER {server_health_level.upper()}</span>
    </div>

    <div class="grid" style="margin-top:12px">
      {server_cards_html}
    </div>

    <div class="serverSplit" style="margin-top:14px">
      <div>
        <h2>Server warnings / signals</h2>
        <div class="tableWrap">
          <table>
            <thead><tr><th>Signal</th><th>Value</th><th>Detail</th></tr></thead>
            <tbody>{server_signal_rows}</tbody>
          </table>
        </div>
      </div>

      <div>
        <h2>Server knobs / limits</h2>
        <div class="tableWrap">
          <table>
            <thead><tr><th>Metric / Env</th><th>Current</th><th>Recommendation</th></tr></thead>
            <tbody>{server_knob_rows}</tbody>
          </table>
        </div>
      </div>
    </div>

    <div style="margin-top:14px">
      <h2>Server-specific recommendations</h2>
      <div class="tableWrap">
        <table>
          <thead><tr><th>Recommendation</th><th>Issue</th><th>Action</th><th>Suggested env / command</th></tr></thead>
          <tbody>{server_recommendation_rows}</tbody>
        </table>
      </div>
    </div>
  </div>

  <div class="cols">
    <div class="card">
      <h2>Why</h2>
      <ul>{reasons_html}</ul>
    </div>

    <div class="card">
      <h2>Signal score</h2>
      <div>{score_items}</div>
    </div>
  </div>

  <div class="card">
    <h2>Proposed changes</h2>
    <div class="tableWrap">
      <table>
        <thead>
          <tr>
            <th>Area</th>
            <th>Key</th>
            <th>Current</th>
            <th>Suggested</th>
            <th>Impact</th>
            <th>Reason</th>
          </tr>
        </thead>
        <tbody>{''.join(change_rows)}</tbody>
      </table>
    </div>
  </div>

  <div class="card">
    <h2>Site / traffic groups</h2>
    <div class="tableWrap">
      <table>
        <thead><tr><th>Area</th><th>Requests</th><th>Errors</th><th>Error %</th><th>Traffic</th><th>Hosts</th></tr></thead>
        <tbody>{site_group_rows}</tbody>
      </table>
    </div>
  </div>

  <div class="card">
    <h2>Host-level analysis</h2>
    <div class="tableWrap">
      <table>
        <thead><tr><th>Host</th><th>Category</th><th>Req</th><th>Err</th><th>Err %</th><th>Traffic</th><th>Issue</th><th>Recommendation</th></tr></thead>
        <tbody>{site_rows}</tbody>
      </table>
    </div>
  </div>

  <div class="cols">
    <div class="card">
      <h2>Apps Script health</h2>
      <p class="muted">
        Total requests: <b>{_e(script_summary.get('total_requests', '-'))}</b>,
        errors: <b>{_e(script_summary.get('total_errors', '-'))}</b>,
        error rate: <b>{_e(script_summary.get('error_rate', '-'))}%</b>
      </p>
      <div class="tableWrap">
        <table>
          <thead><tr><th>Script</th><th>Req</th><th>Err</th><th>Err %</th><th>Quota %</th><th>Last</th><th>Issue</th></tr></thead>
          <tbody>{script_rows}</tbody>
        </table>
      </div>
    </div>

    <div class="card">
      <h2>Feature state</h2>
      <div class="tableWrap">
        <table>
          <thead><tr><th>Feature</th><th>State</th><th>Global off</th><th>Details</th></tr></thead>
          <tbody>{feature_rows}</tbody>
        </table>
      </div>
    </div>
  </div>

  <div class="card">
    <h2>Runtime knobs</h2>
    <div class="muted">Live tuning values used by the new HLS/H2/range analysis.</div>
    <div class="tableWrap">
      <table>
        <thead><tr><th>Key</th><th>Value</th></tr></thead>
        <tbody>{runtime_knob_rows}</tbody>
      </table>
    </div>
  </div>

  <div class="card">
    <details>
      <summary>Raw analysis JSON</summary>
      <pre>{raw_json}</pre>
    </details>
  </div>
</main>


<script>

</script>

<script>







/* PATCH_SUGGESTIONS_FINAL_I18N_JS_START */
(function(){{
  if(window.__MHR_SUGGESTIONS_FINAL_I18N__) return;
  window.__MHR_SUGGESTIONS_FINAL_I18N__ = true;

  const FA = {{
    "MHR Suggestions / Advisor":"پیشنهادهای MHR / مشاور",
    "Multi-layer analysis for video, web browsing, relay pressure, Apps Script health, load failures and feature states.":"تحلیل چندلایه برای ویدیو، مرور وب، فشار رله، سلامت Apps Script، خطاهای بارگذاری و وضعیت قابلیت‌ها.",
    "Version:":"نسخه:",
    "Apply to Auto mode":"اعمال روی حالت خودکار",
    "Refresh":"رفرش",
    "Status:":"وضعیت:",
    "Analyzing data...":"در حال تحلیل داده‌ها...",
    "Auto refresh in":"رفرش خودکار تا",
    "Tip: keep browsing, watching videos, downloading, and doing your normal work so MHR can collect enough traffic data and analyze it for you :)":"نکته: مرور، تماشای ویدیو، دانلود و کار عادی‌ات را ادامه بده تا MHR داده ترافیکی کافی جمع کند و برایت تحلیل کند :)",
    "Error rate:":"نرخ خطا:",
    "Requests:":"درخواست‌ها:",

    "HEALTHY":"سالم",
    "WARNING":"هشدار",
    "CRITICAL":"بحرانی",
    "SERVER HEALTHY":"سرور سالم",
    "SERVER WARNING":"هشدار سرور",
    "SERVER CRITICAL":"سرور بحرانی",

    "Overall":"کلی",
    "Video / HLS":"ویدیو / HLS",
    "H2":"H2",
    "Relay":"رله",
    "Scripts":"اسکریپت‌ها",
    "Exit inflight":"پردازش همزمان نود خروجی",
    "Exit Node":"نود خروجی",
    "Server Memory":"حافظه سرور",
    "Inflight":"در حال پردازش",
    "Server Errors":"خطاهای سرور",
    "Memory Cleanups":"پاکسازی‌های حافظه",
    "Server Quality":"کیفیت سرور",
    "Front IP":"IP فرانت",
    "SNI":"SNI",
    "H2 Front":"فرانت H2",
    "Front Quality":"کیفیت فرانت",

    "Exit Node / Server Health":"سلامت نود خروجی / سرور",
    "This section uses only the latest server health snapshot already collected by main.py.":"این بخش فقط از آخرین اسنپ‌شات سلامت سرور که قبلاً توسط main.py جمع‌آوری شده استفاده می‌کند.",
    "It does not send a new request to the exit node.":"درخواست جدیدی به نود خروجی ارسال نمی‌کند.",
    "Server warnings / signals":"هشدارها / سیگنال‌های سرور",
    "Server knobs / limits":"تنظیمات / محدودیت‌های سرور",
    "Server-specific recommendations":"پیشنهادهای مخصوص سرور",

    "Signal":"سیگنال",
    "Value":"مقدار",
    "Detail":"جزئیات",
    "Metric / Env":"متریک / Env",
    "Current":"فعلی",
    "Recommendation":"پیشنهاد",
    "Action":"اقدام",
    "Suggested env / command":"Env / دستور پیشنهادی",
    "Issue":"مشکل",
    "Title":"عنوان",
    "Recommended":"پیشنهادی",

    "Why":"دلیل‌ها",
    "Signal score":"امتیاز سیگنال‌ها",
    "Proposed changes":"تغییرات پیشنهادی",
    "Site / traffic groups":"گروه‌های سایت / ترافیک",
    "Host-level analysis":"تحلیل در سطح هاست",
    "Apps Script health":"سلامت Apps Script",
    "Feature state":"وضعیت قابلیت‌ها",
    "Runtime knobs":"تنظیمات زمان اجرا",
    "Raw analysis JSON":"JSON خام تحلیل",

    "Area":"بخش",
    "Key":"کلید",
    "Suggested":"پیشنهادی",
    "Impact":"اثر",
    "Reason":"دلیل",
    "Errors":"خطاها",
    "Error %":"درصد خطا",
    "Traffic":"ترافیک",
    "Hosts":"هاست‌ها",
    "Host":"هاست",
    "Category":"دسته",
    "Req":"درخواست",
    "Err":"خطا",
    "Err %":"درصد خطا",
    "Script":"اسکریپت",
    "Quota %":"درصد سهمیه",
    "Last":"آخرین",
    "Feature":"قابلیت",
    "State":"وضعیت",
    "Global off":"خاموشی سراسری",
    "Details":"جزئیات",

    "No issues detected.":"مشکلی شناسایی نشد.",
    "No log signals yet":"هنوز سیگنال لاگی وجود ندارد",
    "No summary available.":"خلاصه‌ای موجود نیست.",
    "No server health snapshot is available yet.":"هنوز اسنپ‌شات سلامت سرور موجود نیست.",
    "No server signals yet.":"هنوز سیگنال سرور وجود ندارد.",
    "No server knobs yet.":"هنوز تنظیمات سرور وجود ندارد.",
    "No server-specific recommendations.":"پیشنهاد مخصوص سرور وجود ندارد.",
    "No changes suggested.":"تغییری پیشنهاد نشده است.",
    "No site groups yet.":"هنوز گروه سایت وجود ندارد.",
    "No host-level data yet.":"هنوز داده سطح هاست وجود ندارد.",
    "No script data yet.":"هنوز داده اسکریپت وجود ندارد.",
    "No feature data yet.":"هنوز داده قابلیت وجود ندارد.",
    "No runtime knobs yet.":"هنوز تنظیمات زمان اجرا وجود ندارد.",
    "No Data":"داده‌ای نیست",

    "healthy":"سالم",
    "warning":"هشدار",
    "critical":"بحرانی",
    "ONLINE":"آنلاین",
    "OFFLINE":"آفلاین",
    "ON":"روشن",
    "OFF":"خاموش",
    "YES":"بله",
    "NO":"خیر",
    "Status":"وضعیت",
    "General":"عمومی",
    "Stability":"پایداری",
    "Connection stability":"پایداری اتصال",

    "Monitoring only. Current runtime errors are not high enough for aggressive tuning.":"فقط مانیتورینگ. خطاهای زمان اجرای فعلی برای تیونینگ تهاجمی کافی نیست.",
    "No changes needed right now. Current runtime data does not justify tuning.":"فعلاً تغییری لازم نیست. داده‌های زمان اجرای فعلی تیونینگ را توجیه نمی‌کند.",
    "Warning-level runtime issues detected. Suggestions are conservative and avoid disabling features.":"مشکلات سطح هشدار در زمان اجرا شناسایی شد. پیشنهادها محافظه‌کارانه هستند و از غیرفعال کردن قابلیت‌ها خودداری می‌کنند.",
    "Critical current-runtime condition detected. Suggestions keep features alive but reduce pressure and raise timeouts.":"وضعیت بحرانی در زمان اجرای فعلی شناسایی شد. پیشنهادها قابلیت‌ها را روشن نگه می‌دارند اما فشار را کم کرده و تایم‌اوت‌ها را افزایش می‌دهند.",
    "Analyzing current runtime data. Keep browsing normally so MHR can collect a clean sample.":"در حال تحلیل داده‌های زمان اجرای فعلی. عادی مرور کن تا MHR نمونه تمیز جمع‌آوری کند.",
    "Current runtime window looks usable.":"پنجره زمان اجرای فعلی قابل استفاده به نظر می‌رسد.",

    "YouTube / video":"یوتیوب / ویدیو",
    "HLS video CDN":"CDN ویدیوی HLS",
    "Google services / APIs":"سرویس‌ها / APIهای گوگل",
    "General web browsing":"مرور عمومی وب",
    "Exit node health path":"مسیر سلامت نود خروجی",
    "Local / LAN":"محلی / LAN",
    "web":"وب",
    "youtube":"یوتیوب",
    "hls_video":"ویدیوی HLS",
    "google_services":"سرویس‌های گوگل",
    "exit_node":"نود خروجی",
    "local":"محلی",
    "unknown":"نامشخص",

    "Looks stable.":"پایدار به نظر می‌رسد.",
    "No host-specific change needed.":"تغییر مخصوص این هاست لازم نیست.",
    "Check runtime/recent errors before applying changes.":"قبل از اعمال تغییرات، خطاهای اخیر/زمان اجرا را بررسی کن.",
    "Monitor recent behavior; old errors may be stale.":"رفتار اخیر را مانیتور کن؛ خطاهای قدیمی ممکن است کهنه باشند.",
    "Healthy in the current runtime window.":"در پنجره زمان اجرای فعلی سالم است.",
    "Current status/rate/quota indicates a real problem.":"وضعیت/نرخ/سهمیه فعلی نشان‌دهنده مشکل واقعی است.",
    "High runtime error rate in the current window.":"نرخ خطای زمان اجرا در پنجره فعلی بالاست.",
    "Runtime errors exist, but this is not critical yet.":"خطاهای زمان اجرا وجود دارد، اما هنوز بحرانی نیست.",
    "Historical errors exist, but runtime window is currently OK.":"خطاهای تاریخی وجود دارد، اما پنجره زمان اجرای فعلی OK است.",

    "Exit node looks healthy from the latest dashboard snapshot.":"طبق آخرین اسنپ‌شات داشبورد، نود خروجی سالم به نظر می‌رسد.",
    "No exit-node health snapshot is available yet.":"هنوز اسنپ‌شات سلامت نود خروجی موجود نیست.",
    "Exit node health is not OK or the node is offline/locked.":"سلامت نود خروجی OK نیست یا نود آفلاین/قفل‌شده است.",
    "Exit node memory pressure is critical.":"فشار حافظه نود خروجی بحرانی است.",
    "Exit node memory is above the soft limit.":"حافظه نود خروجی بالاتر از محدودیت نرم است.",
    "Front IP / SNI path looks healthy from the current snapshot.":"طبق اسنپ‌شات فعلی، مسیر Front IP / SNI سالم به نظر می‌رسد.",
    "Some Front IP timeout signals were detected. Check route/SNI before changing MHR tuning.":"چند سیگنال تایم‌اوت Front IP شناسایی شد. قبل از تغییر تیونینگ MHR، مسیر/SNI را بررسی کن.",
    "Front IP / SNI path has warnings. Check the dedicated Front IP / SNI section before changing MHR tuning.":"مسیر Front IP / SNI هشدار دارد. قبل از تغییر تیونینگ MHR، بخش اختصاصی Front IP / SNI را بررسی کن.",
    "Exit-node/server health has warnings. See the dedicated Server Health section.":"سلامت نود خروجی/سرور هشدار دارد. بخش اختصاصی سلامت سرور را ببین.",
    "Exit-node/server health is critical. See the dedicated Server Health section for PM2/env recommendations.":"سلامت نود خروجی/سرور بحرانی است. برای پیشنهادهای PM2/env بخش سلامت سرور را ببین.",

    "Safe Mode is active: H2, Turbo, SABR, Video Prefetch, Manifest Prefetch and Video Passthrough are all off.":"حالت امن فعال است: H2، توربو، SABR، پیش‌بارگذاری ویدیو، پیش‌بارگذاری مانیفست و عبور مستقیم ویدیو همگی خاموش هستند.",
    "Suggestions will not re-enable dashboard-disabled features automatically.":"پیشنهادها قابلیت‌هایی را که از داشبورد غیرفعال شده‌اند به‌صورت خودکار دوباره فعال نمی‌کنند.",
    "Apply these advanced suggestions to Auto mode?":"این پیشنهادهای پیشرفته روی حالت خودکار اعمال شوند؟",
    "Applied to Auto mode":"روی حالت خودکار اعمال شد",
    "failed":"ناموفق"
  }};

  const RE = [
    [/Runtime:\\s*(\\d+)\\s*errors over\\s*(\\d+)\\s*requests\\s*\\(([^)]+)\\)\\.\\s*Total historical:\\s*(\\d+)\\/(\\d+)\\s*\\(([^)]+)\\)\\./g,
      "زمان اجرا: $1 خطا در $2 درخواست ($3). کل تاریخی: $4/$5 ($6)."],
    [/HLS segments=(\\d+), prefetch=(\\d+), hits=(\\d+), range_probe=(\\d+)\\./g,
      "سگمنت‌های HLS=$1، پیش‌بارگذاری=$2، برخوردها=$3، بررسی بازه=$4."],
    [/H2 timeout=(\\d+), H2 errors=(\\d+), reconnect=(\\d+)\\./g,
      "تایم‌اوت H2=$1، خطاهای H2=$2، اتصال مجدد=$3."],
    [/Timeouts=(\\d+), H1=(\\d+), relay errors=(\\d+), bad statuses=(\\d+)\\./g,
      "تایم‌اوت‌ها=$1، H1=$2، خطاهای رله=$3، وضعیت‌های بد=$4."],
    [/Runtime script errors:\\s*(\\d+)\\s*over\\s*(\\d+)\\s*requests\\./g,
      "خطاهای اسکریپت در زمان اجرا: $1 در $2 درخواست."],
    [/High inflight means requests may be stuck or waiting too long\\./g,
      "پردازش همزمان بالا یعنی ممکن است درخواست‌ها گیر کرده باشند یا بیش از حد منتظر بمانند."],
    [/Uptime\\s*(\\d+)s,\\s*requests\\s*(\\d+)\\./g,
      "زمان کارکرد $1 ثانیه، درخواست‌ها $2."],
    [/(\\d+)\\s*errors over\\s*(\\d+)\\s*requests\\./g,
      "$1 خطا در $2 درخواست."],
    [/Heap\\s*([0-9.]+)MB,\\s*external\\s*([0-9.]+)MB,\\s*hard limit\\s*([0-9.]+)MB\\./g,
      "Heap $1MB، حافظه خارجی $2MB، محدودیت سخت $3MB."],
    [/Approx\\s*([0-9.]+)\\/hour\\./g,
      "حدود $1 در ساعت."],
    [/Active requests currently handled by the exit node\\./g,
      "درخواست‌های فعالی که هم‌اکنون توسط نود خروجی پردازش می‌شوند."],
    [/Historical errors reported by the exit-node process\\./g,
      "خطاهای تاریخی گزارش‌شده توسط پردازش نود خروجی."],
    [/Current active requests on the exit node\\./g,
      "درخواست‌های فعال فعلی روی نود خروجی."],
    [/Bytes seen by the exit-node relay process\\./g,
      "بایت‌های دیده‌شده توسط پردازش رله نود خروجی."],
    [/Live tuning values used by the new HLS\\/H2\\/range analysis\\./g,
      "مقادیر تیونینگ زنده که در تحلیل جدید HLS/H2/Range استفاده می‌شوند."],
    [/Total requests:/g, "کل درخواست‌ها:"],
    [/errors:/g, "خطاها:"],
    [/error rate:/g, "نرخ خطا:"],
    [/Status:/g, "وضعیت:"],
    [/Version:/g, "نسخه:"],
    [/Requests:/g, "درخواست‌ها:"],
    [/Error rate:/g, "نرخ خطا:"],
    [/Auto refresh in/g, "رفرش خودکار تا"],
    [/\\bONLINE\\b/g, "آنلاین"],
    [/\\bOFFLINE\\b/g, "آفلاین"],
    [/\\bON\\b/g, "روشن"],
    [/\\bOFF\\b/g, "خاموش"],
    [/\\bYES\\b/g, "بله"],
    [/\\bNO\\b/g, "خیر"]
  ];

  function normalizeLang(v){{
    v = String(v || "en").toLowerCase();
    return v.indexOf("fa") === 0 ? "fa" : "en";
  }}

  function getUrlLang(){{
    try {{
      return new URLSearchParams(window.location.search).get("lang");
    }} catch(e) {{
      return null;
    }}
  }}

  function getLang(){{
    return normalizeLang(
      window.MHR_LANG ||
      getUrlLang() ||
      localStorage.getItem("mhr.lang") ||
      localStorage.getItem("mhr.language") ||
      document.documentElement.getAttribute("lang") ||
      "en"
    );
  }}

  function syncUrlLang(lang){{
    lang = normalizeLang(lang);
    try {{
      const url = new URL(window.location.href);
      url.searchParams.set("lang", lang);
      window.history.replaceState(null, "", url.toString());
    }} catch(e) {{}}
  }}

  function trText(value){{
    if(value == null) return value;
    let out = String(value);

    if(FA[out]) return FA[out];

    Object.keys(FA)
      .sort((a,b) => b.length - a.length)
      .forEach(k => {{
        if(!k || k.length < 2) return;
        out = out.split(k).join(FA[k]);
      }});

    RE.forEach(pair => {{
      out = out.replace(pair[0], pair[1]);
    }});

    return out;
  }}

  function shouldSkipTextNode(node){{
    let el = node.parentElement;
    while(el){{
      const tag = el.tagName;
      if(tag === "SCRIPT" || tag === "STYLE" || tag === "TEXTAREA" || tag === "PRE" || tag === "CODE"){{
        return true;
      }}
      el = el.parentElement;
    }}
    return false;
  }}

  function rememberOriginals(){{
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    while(walker.nextNode()){{
      const n = walker.currentNode;
      if(shouldSkipTextNode(n)) continue;
      if(n.__mhrOriginalText === undefined){{
        n.__mhrOriginalText = n.nodeValue;
      }}
    }}

    document.querySelectorAll("[title]").forEach(el => {{
      if(el.__mhrOriginalTitle === undefined){{
        el.__mhrOriginalTitle = el.getAttribute("title") || "";
      }}
    }});
  }}

  function paintLangButton(lang){{
    const btn = document.getElementById("mhrLangBtn") || document.getElementById("langBtn");
    if(!btn) return;
    btn.id = "mhrLangBtn";
    btn.textContent = lang === "fa" ? "EN" : "FA";
    btn.title = lang === "fa" ? "English" : "فارسی";
    btn.setAttribute("aria-label", btn.title);
  }}

  function applyI18n(lang){{
    lang = normalizeLang(lang || getLang());
    const isFa = lang === "fa";

    window.MHR_LANG = lang;
    localStorage.setItem("mhr.lang", lang);
    localStorage.setItem("mhr.language", lang);
    syncUrlLang(lang);

    document.documentElement.lang = lang;
    document.documentElement.dir = isFa ? "rtl" : "ltr";

    if(document.body){{
      document.body.dir = isFa ? "rtl" : "ltr";
      document.body.classList.toggle("rtl", isFa);
      document.body.classList.toggle("ltr", !isFa);
    }}

    rememberOriginals();

    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while(walker.nextNode()) nodes.push(walker.currentNode);

    nodes.forEach(n => {{
      if(shouldSkipTextNode(n)) return;
      if(n.__mhrOriginalText === undefined) n.__mhrOriginalText = n.nodeValue;
      const original = n.__mhrOriginalText;
      n.nodeValue = isFa ? trText(original) : original;
    }});

    document.querySelectorAll("[title]").forEach(el => {{
      if(el.__mhrOriginalTitle === undefined){{
        el.__mhrOriginalTitle = el.getAttribute("title") || "";
      }}
      el.setAttribute("title", isFa ? trText(el.__mhrOriginalTitle) : el.__mhrOriginalTitle);
    }});

    paintLangButton(lang);
  }}

  window.MHR_applySuggestionsI18n = applyI18n;

  window.toggleLang = function(){{
    const next = getLang() === "fa" ? "en" : "fa";
    applyI18n(next);
  }};

  window.safeReload = function(){{
    applyI18n(getLang());
    window.location.reload();
  }};

  document.addEventListener("click", function(e){{
    const btn = e.target && e.target.closest ? e.target.closest("#mhrLangBtn,#langBtn") : null;
    if(!btn) return;
    e.preventDefault();
    e.stopPropagation();
    window.toggleLang();
  }}, true);

  applyI18n(getLang());
  setTimeout(() => applyI18n(getLang()), 250);
  setTimeout(() => applyI18n(getLang()), 1000);
}})();
/* PATCH_SUGGESTIONS_FINAL_I18N_JS_END */

// MHR_AUTO_REFRESH_TIMER
(function(){{
  let remaining = Number("{next_refresh}") || 60;
  if (!Number.isFinite(remaining) || remaining < 1) remaining = 60;

  const el = document.getElementById("refreshCountdown");

  function paint(){{
    if (el) el.textContent = String(Math.max(0, Math.floor(remaining)));
  }}

  paint();

  const timer = setInterval(function(){{
    remaining -= 1;
    paint();

    if (remaining <= 0){{
      clearInterval(timer);
      safeReload();
    }}
  }}, 1000);
}})();

async function applySuggestion(){{
  if(!confirm((window.MHR_applySuggestionsI18n && document.documentElement.lang === "fa") ? "این پیشنهادهای پیشرفته روی حالت خودکار اعمال شوند؟" : "Apply these advanced suggestions to Auto mode?")) return;
  const r = await fetch("/apply-suggestion", {{
    method:"POST",
    headers:{{"Content-Type":"application/json"}},
    body:"{{}}"
  }});
  const j = await r.json();
  if(!j.ok) alert(j.error || ((document.documentElement.lang === "fa") ? "ناموفق" : "failed"));
  else alert((document.documentElement.lang === "fa") ? "روی حالت خودکار اعمال شد" : "Applied to Auto mode");
  safeReload();
}}
</script>

<script>
/* PATCH_SUGGESTIONS_SHARED_LANG_FORCE_START */
(function(){{
  if(window.__MHR_SUGGESTIONS_SHARED_LANG_FORCE__) return;
  window.__MHR_SUGGESTIONS_SHARED_LANG_FORCE__ = true;

  function norm(v){{
    v = String(v || "en").toLowerCase();
    return v.indexOf("fa") === 0 ? "fa" : "en";
  }}

  function sharedLang(){{
    return norm(
      localStorage.getItem("mhr.lang") ||
      localStorage.getItem("mhr.language") ||
      window.MHR_LANG ||
      document.documentElement.getAttribute("lang") ||
      "en"
    );
  }}

  function syncUrl(lang){{
    try {{
      var u = new URL(window.location.href);
      u.searchParams.set("lang", lang);
      history.replaceState(null, "", u.toString());
    }} catch(e) {{}}
  }}

  function setSharedLang(lang){{
    lang = norm(lang);
    window.MHR_LANG = lang;

    try {{
      localStorage.setItem("mhr.lang", lang);
      localStorage.setItem("mhr.language", lang);

      // اگر پچ‌های قبلی کلید جدا ساخته باشند، این‌ها را هم همگام می‌کنیم
      localStorage.setItem("mhr.suggestions.lang", lang);
      localStorage.setItem("mhr.downloader.lang", lang);
      localStorage.setItem("mhr.dashboard.lang", lang);
    }} catch(e) {{}}

    syncUrl(lang);

    var rtl = lang === "fa";
    document.documentElement.setAttribute("lang", lang);
    document.documentElement.setAttribute("dir", rtl ? "rtl" : "ltr");

    if(document.body){{
      document.body.setAttribute("dir", rtl ? "rtl" : "ltr");
      document.body.classList.toggle("rtl", rtl);
      document.body.classList.toggle("ltr", !rtl);
    }}

    var btns = [
      document.getElementById("langBtn"),
      document.getElementById("mhrLangBtn"),
      document.getElementById("mhrSuggestionsLangBtn")
    ].filter(Boolean);

    btns.forEach(function(btn){{
      btn.textContent = rtl ? "EN" : "FA";
      btn.title = rtl ? "English" : "فارسی";
      btn.setAttribute("aria-label", btn.title);
    }});

    // صدا زدن همه i18n های قبلی، چون در نسخه‌های قبلی چند اسم مختلف ساخته شده
    try {{
      if(typeof window.MHR_applySuggestionsI18n === "function") {{
        window.MHR_applySuggestionsI18n(lang);
      }}
    }} catch(e) {{}}

    try {{
      if(typeof window.MHR_suggestionsApplyI18n === "function") {{
        window.MHR_suggestionsApplyI18n(lang);
      }}
    }} catch(e) {{}}

    try {{
      if(typeof window.MHR_setSuggestionsLangStable === "function") {{
        window.MHR_setSuggestionsLangStable(lang);
      }}
    }} catch(e) {{}}
  }}

  window.MHR_setSharedLang = setSharedLang;

  // همه دکمه‌های زبان پیشنهادها را مجبور می‌کنیم کلید مشترک را عوض کنند
  document.addEventListener("click", function(e){{
    var btn = e.target && e.target.closest
      ? e.target.closest("#langBtn, #mhrLangBtn, #mhrSuggestionsLangBtn")
      : null;

    if(!btn) return;

    e.preventDefault();
    e.stopPropagation();

    var next = sharedLang() === "fa" ? "en" : "fa";
    setSharedLang(next);
  }}, true);

  // اگر زبان در داشبورد یا دانلودر تغییر کرد، پیشنهادها هم همان لحظه هماهنگ شود
  window.addEventListener("storage", function(e){{
    if(e.key === "mhr.lang" || e.key === "mhr.language"){{
      setSharedLang(sharedLang());
    }}
  }});

  // قبل از رفرش خودکار، زبان مشترک را داخل URL هم می‌گذاریم تا 404/برگشت زبان ندهد
  window.MHR_beforeSuggestionsReload = function(){{
    setSharedLang(sharedLang());
  }};

  // اجرای اولیه
  setSharedLang(sharedLang());

  // چون صفحه پیشنهادها بعضی متن‌ها را بعداً دوباره paint می‌کند
  setTimeout(function(){{ setSharedLang(sharedLang()); }}, 100);
  setTimeout(function(){{ setSharedLang(sharedLang()); }}, 500);
  setTimeout(function(){{ setSharedLang(sharedLang()); }}, 1200);
}})();
/* PATCH_SUGGESTIONS_SHARED_LANG_FORCE_END */
</script>

<script>
/* PATCH_SUGGESTIONS_LANG_BUTTON_HARD_FIX_START */
(function(){{
  if(window.__MHR_SUGGESTIONS_LANG_BUTTON_HARD_FIX__) return;
  window.__MHR_SUGGESTIONS_LANG_BUTTON_HARD_FIX__ = true;

  const FA = {{
    "MHR Suggestions / Advisor":"پیشنهادهای MHR / مشاور",
    "Apply to Auto mode":"اعمال روی حالت خودکار",
    "Refresh":"رفرش",
    "Status:":"وضعیت:",
    "Analyzing data...":"در حال تحلیل داده‌ها...",
    "Auto refresh in":"رفرش خودکار تا",
    "Tip: keep browsing, watching videos, downloading, and doing your normal work so MHR can collect enough traffic data and analyze it for you :)":"نکته: مرور، تماشای ویدیو، دانلود و کار عادی‌ات را ادامه بده تا MHR داده کافی جمع کند و تحلیل کند :)",
    "Error rate:":"نرخ خطا:",
    "Requests:":"درخواست‌ها:",
    "HEALTHY":"سالم",
    "WARNING":"هشدار",
    "CRITICAL":"بحرانی",
    "SERVER HEALTHY":"سرور سالم",
    "SERVER WARNING":"هشدار سرور",
    "SERVER CRITICAL":"سرور بحرانی",
    "Overall":"کلی",
    "Video / HLS":"ویدیو / HLS",
    "Relay":"رله",
    "Scripts":"اسکریپت‌ها",
    "Exit inflight":"درخواست‌های همزمان نود خروجی",
    "Exit Node / Server Health":"سلامت نود خروجی / سرور",
    "Server warnings / signals":"هشدارها / سیگنال‌های سرور",
    "Server knobs / limits":"تنظیمات / محدودیت‌های سرور",
    "Server-specific recommendations":"پیشنهادهای مخصوص سرور",
    "Why":"دلیل‌ها",
    "Signal score":"امتیاز سیگنال‌ها",
    "Proposed changes":"تغییرات پیشنهادی",
    "Site / traffic groups":"گروه‌های سایت / ترافیک",
    "Host-level analysis":"تحلیل هاست‌ها",
    "Apps Script health":"سلامت Apps Script",
    "Feature state":"وضعیت قابلیت‌ها",
    "Runtime knobs":"تنظیمات زمان اجرا",
    "Raw analysis JSON":"JSON خام تحلیل",
    "Signal":"سیگنال",
    "Value":"مقدار",
    "Detail":"جزئیات",
    "Metric / Env":"متریک / Env",
    "Current":"فعلی",
    "Recommendation":"پیشنهاد",
    "Action":"اقدام",
    "Suggested env / command":"Env / دستور پیشنهادی",
    "Area":"بخش",
    "Key":"کلید",
    "Suggested":"پیشنهادی",
    "Impact":"اثر",
    "Reason":"دلیل",
    "Errors":"خطاها",
    "Error %":"درصد خطا",
    "Traffic":"ترافیک",
    "Hosts":"هاست‌ها",
    "Host":"هاست",
    "Category":"دسته",
    "Req":"درخواست",
    "Err":"خطا",
    "Err %":"درصد خطا",
    "Script":"اسکریپت",
    "Quota %":"درصد سهمیه",
    "Last":"آخرین",
    "Feature":"قابلیت",
    "State":"وضعیت",
    "Global off":"خاموشی سراسری",
    "Details":"جزئیات",
    "No issues detected.":"مشکلی شناسایی نشد.",
    "No changes suggested.":"تغییری پیشنهاد نشده است.",
    "No Data":"داده‌ای نیست",
    "healthy":"سالم",
    "warning":"هشدار",
    "critical":"بحرانی",
    "ONLINE":"آنلاین",
    "OFFLINE":"آفلاین",
    "ON":"روشن",
    "OFF":"خاموش",
    "YES":"بله",
    "NO":"خیر"
  }};

  const RE = [
    [/Version:/g, "نسخه:"],
    [/Status:/g, "وضعیت:"],
    [/Error rate:/g, "نرخ خطا:"],
    [/Requests:/g, "درخواست‌ها:"],
    [/Auto refresh in/g, "رفرش خودکار تا"],
    [/\\bHEALTHY\\b/g, "سالم"],
    [/\\bWARNING\\b/g, "هشدار"],
    [/\\bCRITICAL\\b/g, "بحرانی"],
    [/\\bONLINE\\b/g, "آنلاین"],
    [/\\bOFFLINE\\b/g, "آفلاین"],
    [/\\bON\\b/g, "روشن"],
    [/\\bOFF\\b/g, "خاموش"],
    [/\\bYES\\b/g, "بله"],
    [/\\bNO\\b/g, "خیر"]
  ];

  function normLang(v){{
    v = String(v || "en").toLowerCase();
    return v.indexOf("fa") === 0 ? "fa" : "en";
  }}

  function getLang(){{
    try{{
      const urlLang = new URLSearchParams(location.search).get("lang");
      return normLang(urlLang || localStorage.getItem("mhr.lang") || localStorage.getItem("mhr.language") || "en");
    }}catch(e){{
      return normLang(localStorage.getItem("mhr.lang") || "en");
    }}
  }}

  function setUrlLang(lang){{
    try{{
      const u = new URL(location.href);
      u.searchParams.set("lang", lang);
      history.replaceState(null, "", u.toString());
    }}catch(e){{}}
  }}

  function translateText(s){{
    let out = String(s ?? "");
    if(FA[out]) return FA[out];

    Object.keys(FA).sort((a,b) => b.length - a.length).forEach(function(k){{
      if(k) out = out.split(k).join(FA[k]);
    }});

    RE.forEach(function(pair){{
      out = out.replace(pair[0], pair[1]);
    }});

    return out;
  }}

  function skipNode(node){{
    let el = node.parentElement;
    while(el){{
      const tag = el.tagName;
      if(tag === "SCRIPT" || tag === "STYLE" || tag === "TEXTAREA" || tag === "PRE" || tag === "CODE"){{
        return true;
      }}
      el = el.parentElement;
    }}
    return false;
  }}

  function rememberOriginals(){{
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    while(walker.nextNode()){{
      const n = walker.currentNode;
      if(skipNode(n)) continue;
      if(n.__mhrOriginalText === undefined){{
        n.__mhrOriginalText = n.nodeValue;
      }}
    }}

    document.querySelectorAll("[title]").forEach(function(el){{
      if(el.__mhrOriginalTitle === undefined){{
        el.__mhrOriginalTitle = el.getAttribute("title") || "";
      }}
    }});
  }}

  function paintButton(lang){{
    const btn =
      document.getElementById("mhrLangBtn") ||
      document.getElementById("langBtn") ||
      document.querySelector(".langMini");

    if(!btn) return;

    btn.id = "mhrLangBtn";
    btn.textContent = lang === "fa" ? "EN" : "FA";
    btn.title = lang === "fa" ? "English" : "فارسی";
    btn.setAttribute("aria-label", btn.title);

    btn.removeAttribute("onclick");
    btn.onclick = null;
  }}

  function applyLang(lang){{
    lang = normLang(lang);
    const isFa = lang === "fa";

    window.MHR_LANG = lang;
    localStorage.setItem("mhr.lang", lang);
    localStorage.setItem("mhr.language", lang);
    localStorage.setItem("mhr.suggestions.lang", lang);

    setUrlLang(lang);

    document.documentElement.lang = lang;
    document.documentElement.dir = isFa ? "rtl" : "ltr";

    if(document.body){{
      document.body.dir = isFa ? "rtl" : "ltr";
      document.body.classList.toggle("rtl", isFa);
      document.body.classList.toggle("ltr", !isFa);
    }}

    rememberOriginals();

    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while(walker.nextNode()) nodes.push(walker.currentNode);

    nodes.forEach(function(n){{
      if(skipNode(n)) return;
      if(n.__mhrOriginalText === undefined) n.__mhrOriginalText = n.nodeValue;
      n.nodeValue = isFa ? translateText(n.__mhrOriginalText) : n.__mhrOriginalText;
    }});

    document.querySelectorAll("[title]").forEach(function(el){{
      if(el.__mhrOriginalTitle === undefined){{
        el.__mhrOriginalTitle = el.getAttribute("title") || "";
      }}
      el.setAttribute("title", isFa ? translateText(el.__mhrOriginalTitle) : el.__mhrOriginalTitle);
    }});

    paintButton(lang);
  }}

  window.MHR_applySuggestionsLangHardFix = applyLang;

  window.toggleLang = function(){{
    const next = getLang() === "fa" ? "en" : "fa";
    applyLang(next);
  }};

  window.safeReload = function(){{
    const lang = getLang();
    localStorage.setItem("mhr.lang", lang);
    localStorage.setItem("mhr.language", lang);
    setUrlLang(lang);
    location.reload();
  }};

  document.addEventListener("click", function(e){{
    const btn = e.target && e.target.closest
      ? e.target.closest("#mhrLangBtn, #langBtn, .langMini")
      : null;

    if(!btn) return;

    e.preventDefault();
    e.stopPropagation();
    e.stopImmediatePropagation();

    const next = getLang() === "fa" ? "en" : "fa";
    applyLang(next);
  }}, true);

  applyLang(getLang());
  setTimeout(function(){{ applyLang(getLang()); }}, 100);
  setTimeout(function(){{ applyLang(getLang()); }}, 500);
  setTimeout(function(){{ applyLang(getLang()); }}, 1200);
}})();
/* PATCH_SUGGESTIONS_LANG_BUTTON_HARD_FIX_END */
</script>

</body>
</html>"""
