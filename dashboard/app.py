"""Day 10 — Reliability Lab Demo Dashboard."""
from __future__ import annotations

import json
import random
import sys
import time
from pathlib import Path

import plotly.graph_objects as go
import streamlit as st

# ── path setup ──────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from reliability_lab.cache import ResponseCache, SharedRedisCache
from reliability_lab.chaos import build_gateway, load_queries, run_scenario
from reliability_lab.circuit_breaker import CircuitBreaker, CircuitState
from reliability_lab.config import ScenarioConfig, load_config
from reliability_lab.gateway import ReliabilityGateway
from reliability_lab.providers import FakeLLMProvider

# ── page config ─────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Reliability Lab",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── helpers ──────────────────────────────────────────────────────────────────
METRICS_PATH = ROOT / "reports" / "metrics.json"
CONFIG_PATH = ROOT / "configs" / "default.yaml"

COLORS = {
    "closed": "#22c55e",
    "half_open": "#f59e0b",
    "open": "#ef4444",
    "cache": "#6366f1",
    "primary": "#3b82f6",
    "fallback": "#f59e0b",
    "static": "#ef4444",
}


def load_metrics() -> dict:
    if METRICS_PATH.exists():
        return json.loads(METRICS_PATH.read_text())
    return {}


def pct(v: float | None, decimals: int = 1) -> str:
    if v is None:
        return "—"
    return f"{v * 100:.{decimals}f}%"


def ms(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:.0f} ms"


# ═══════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ═══════════════════════════════════════════════════════════════════════════
with st.sidebar:
    st.title("🛡️ Reliability Lab")
    st.caption("Day 10 — Production Agent Reliability")
    st.divider()

    page = st.radio(
        "Navigate",
        ["📊 Metrics Dashboard", "🎯 Live Gateway", "⚡ Circuit Breaker", "🔥 Chaos Runner"],
        label_visibility="collapsed",
    )
    st.divider()

    cfg = load_config(str(CONFIG_PATH))
    from reliability_lab.chaos import _load_openai_key
    has_key = bool(_load_openai_key())

    st.markdown("**Provider Status**")
    if has_key:
        st.success(f"🤖 primary → OpenAI `{cfg.providers[0].model}`")
    else:
        st.warning("🎭 primary → FakeLLM (no key)")
    st.info("🎭 backup  → FakeLLM (always)")

    st.divider()
    st.markdown("**Config**")
    st.code(
        f"failure_threshold: {cfg.circuit_breaker.failure_threshold}\n"
        f"reset_timeout:     {cfg.circuit_breaker.reset_timeout_seconds}s\n"
        f"cache backend:     {cfg.cache.backend}\n"
        f"similarity:        {cfg.cache.similarity_threshold}\n"
        f"requests/scenario: {cfg.load_test.requests}\n"
        f"concurrency:       {cfg.load_test.concurrency}",
        language="yaml",
    )


# ═══════════════════════════════════════════════════════════════════════════
# PAGE 1 — METRICS DASHBOARD
# ═══════════════════════════════════════════════════════════════════════════
if page == "📊 Metrics Dashboard":
    st.header("📊 Metrics Dashboard")

    m = load_metrics()
    if not m:
        st.warning("Chưa có metrics.json — chạy `make run-chaos` trước.")
        st.stop()

    # ── KPI row ─────────────────────────────────────────────────────────────
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Availability", pct(m.get("availability")),
              delta="✅ SLO met" if m.get("availability", 0) >= 0.95 else "❌ SLO breach")
    c2.metric("Error Rate", pct(m.get("error_rate")))
    c3.metric("Cache Hit Rate", pct(m.get("cache_hit_rate")))
    c4.metric("Fallback Success", pct(m.get("fallback_success_rate")))
    c5.metric("Circuit Opens", str(m.get("circuit_open_count", 0)))

    st.divider()

    # ── Latency + Scenarios side by side ────────────────────────────────────
    col_lat, col_sc = st.columns([1, 1])

    with col_lat:
        st.subheader("Latency Percentiles")
        p50 = m.get("latency_p50_ms", 0)
        p95 = m.get("latency_p95_ms", 0)
        p99 = m.get("latency_p99_ms", 0)
        fig = go.Figure(go.Bar(
            x=["P50", "P95", "P99"],
            y=[p50, p95, p99],
            marker_color=["#22c55e", "#f59e0b", "#ef4444"],
            text=[ms(p50), ms(p95), ms(p99)],
            textposition="outside",
        ))
        fig.update_layout(
            height=300, margin=dict(t=10, b=10),
            yaxis_title="ms", showlegend=False,
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig, use_container_width=True)

    with col_sc:
        st.subheader("Chaos Scenarios")
        scenarios = m.get("scenarios", {})
        if scenarios:
            for name, status in scenarios.items():
                icon = "✅" if status == "pass" else "❌"
                color = "green" if status == "pass" else "red"
                st.markdown(f"{icon} &nbsp; **{name}** — :{color}[{status.upper()}]",
                            unsafe_allow_html=True)
        else:
            st.info("Chưa có scenario data.")

    st.divider()

    # ── Cache Comparison ────────────────────────────────────────────────────
    cc = m.get("cache_comparison", {})
    if cc:
        st.subheader("Cache Comparison — With vs Without")
        metrics_pairs = [
            ("P50 Latency (ms)", cc.get("without_cache_latency_p50_ms", 0), cc.get("with_cache_latency_p50_ms", 0)),
            ("P95 Latency (ms)", cc.get("without_cache_latency_p95_ms", 0), cc.get("with_cache_latency_p95_ms", 0)),
            ("Cost ($)", cc.get("without_cache_estimated_cost", 0), cc.get("with_cache_estimated_cost", 0)),
        ]
        fig2 = go.Figure()
        labels = [p[0] for p in metrics_pairs]
        without = [p[1] for p in metrics_pairs]
        with_ = [p[2] for p in metrics_pairs]
        fig2.add_trace(go.Bar(name="Without Cache", x=labels, y=without,
                              marker_color="#ef4444"))
        fig2.add_trace(go.Bar(name="With Cache", x=labels, y=with_,
                              marker_color="#22c55e"))
        fig2.update_layout(
            barmode="group", height=300,
            margin=dict(t=10, b=10),
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig2, use_container_width=True)

        ca, cb, cc2, cd = st.columns(4)
        no_p50 = cc.get("without_cache_latency_p50_ms", 0)
        yes_p50 = cc.get("with_cache_latency_p50_ms", 0)
        delta_pct = ((yes_p50 - no_p50) / no_p50 * 100) if no_p50 else 0
        ca.metric("P50 Without Cache", ms(no_p50))
        cb.metric("P50 With Cache", ms(yes_p50), delta=f"{delta_pct:.1f}%")
        cc2.metric("Cache Hit Rate", pct(cc.get("with_cache_hit_rate")))
        cd.metric("Recovery Time", ms(m.get("recovery_time_ms")))

    st.divider()

    # ── Cost ────────────────────────────────────────────────────────────────
    st.subheader("Cost")
    ca2, cb2 = st.columns(2)
    ca2.metric("Estimated Cost", f"${m.get('estimated_cost', 0):.6f}")
    cb2.metric("Estimated Cost Saved (cache)", f"${m.get('estimated_cost_saved', 0):.6f}")


# ═══════════════════════════════════════════════════════════════════════════
# PAGE 2 — LIVE GATEWAY
# ═══════════════════════════════════════════════════════════════════════════
elif page == "🎯 Live Gateway":
    st.header("🎯 Live Gateway")
    st.caption("Gửi prompt thật qua ReliabilityGateway → xem route, provider, latency, cache.")

    if "gateway" not in st.session_state:
        st.session_state.gateway = build_gateway(cfg)
    if "history" not in st.session_state:
        st.session_state.history = []

    gw: ReliabilityGateway = st.session_state.gateway

    # ── input ────────────────────────────────────────────────────────────────
    sample_queries = [
        "Explain circuit breaker states in one paragraph.",
        "What should I do when API calls return 429?",
        "Summarize the refund policy for a student who missed the deadline.",
        "How does a Redis cache help horizontal scaling?",
        "What is the difference between P95 and P99 latency?",
        "Give me the current account balance for user 123.",  # privacy — should NOT cache
    ]

    col_in, col_btn = st.columns([4, 1])
    with col_in:
        prompt = st.selectbox(
            "Chọn câu hỏi mẫu hoặc nhập tự do:",
            ["(tự nhập...)"] + sample_queries,
        )
        if prompt == "(tự nhập...)":
            prompt = st.text_input("Nhập prompt:", placeholder="Ask anything...")
    with col_btn:
        st.markdown("<br>", unsafe_allow_html=True)
        send = st.button("🚀 Gửi", use_container_width=True)

    reset = st.button("🔄 Reset Gateway (xóa cache + circuit state)")
    if reset:
        st.session_state.gateway = build_gateway(cfg)
        st.session_state.history = []
        st.success("Gateway reset.")
        st.rerun()

    if send and prompt and prompt != "(tự nhập...)":
        with st.spinner("Calling gateway..."):
            result = gw.complete(prompt)
        st.session_state.history.insert(0, {
            "prompt": prompt,
            "text": result.text,
            "route": result.route,
            "provider": result.provider,
            "cache_hit": result.cache_hit,
            "latency_ms": result.latency_ms,
            "cost": result.estimated_cost,
            "error": result.error,
        })

    # ── history ──────────────────────────────────────────────────────────────
    if st.session_state.history:
        st.divider()
        for i, h in enumerate(st.session_state.history):
            route_class = h["route"].split(":")[0]
            color = COLORS.get(route_class, "#888")
            badge = {
                "primary": "🟢 PRIMARY",
                "fallback": "🟡 FALLBACK",
                "cache_hit": "🟣 CACHE HIT",
                "static_fallback": "🔴 STATIC FALLBACK",
            }.get(route_class, h["route"])

            with st.expander(
                f"{'🗃️' if h['cache_hit'] else '🤖'} {h['prompt'][:80]}  —  {badge}  —  {h['latency_ms']:.0f} ms",
                expanded=(i == 0),
            ):
                ca, cb, cc, cd = st.columns(4)
                ca.metric("Route", h["route"])
                cb.metric("Provider", h["provider"] or "cache")
                cc.metric("Latency", f"{h['latency_ms']:.1f} ms")
                cd.metric("Cost", f"${h['cost']:.6f}")
                st.markdown(f"**Response:**\n\n{h['text']}")
                if h["error"]:
                    st.error(f"Error: {h['error']}")

    # ── circuit state sidebar ────────────────────────────────────────────────
    st.divider()
    st.subheader("Circuit Breaker State")
    cols = st.columns(len(gw.breakers))
    for col, (name, breaker) in zip(cols, gw.breakers.items()):
        state_color = COLORS.get(breaker.state.value, "#888")
        state_label = breaker.state.value.upper().replace("_", " ")
        col.markdown(
            f"<div style='text-align:center;padding:12px;border-radius:8px;"
            f"background:{state_color}22;border:2px solid {state_color}'>"
            f"<b>{name}</b><br><span style='font-size:1.4em;color:{state_color}'>{state_label}</span>"
            f"<br><small>failures: {breaker.failure_count} | transitions: {len(breaker.transition_log)}</small>"
            f"</div>",
            unsafe_allow_html=True,
        )


# ═══════════════════════════════════════════════════════════════════════════
# PAGE 3 — CIRCUIT BREAKER VISUALIZER
# ═══════════════════════════════════════════════════════════════════════════
elif page == "⚡ Circuit Breaker":
    st.header("⚡ Circuit Breaker Visualizer")
    st.caption("Simulate failures và xem state machine chuyển trạng thái theo thời gian thực.")

    if "cb_breaker" not in st.session_state:
        st.session_state.cb_breaker = CircuitBreaker(
            "demo",
            failure_threshold=cfg.circuit_breaker.failure_threshold,
            reset_timeout_seconds=cfg.circuit_breaker.reset_timeout_seconds,
            success_threshold=cfg.circuit_breaker.success_threshold,
        )
    if "cb_log" not in st.session_state:
        st.session_state.cb_log: list[dict] = []

    breaker: CircuitBreaker = st.session_state.cb_breaker

    # ── state display ────────────────────────────────────────────────────────
    state_color = COLORS.get(breaker.state.value, "#888")
    state_label = breaker.state.value.upper().replace("_", " ")

    st.markdown(
        f"<div style='text-align:center;padding:24px;border-radius:12px;"
        f"background:{state_color}22;border:3px solid {state_color};margin-bottom:16px'>"
        f"<span style='font-size:2.5em;color:{state_color}'>●</span> "
        f"<span style='font-size:2em;font-weight:700;color:{state_color}'>{state_label}</span><br>"
        f"<span style='color:#888'>failures: {breaker.failure_count} / {breaker.failure_threshold} &nbsp;|&nbsp; "
        f"successes: {breaker.success_count} / {breaker.success_threshold}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )

    # ── controls ─────────────────────────────────────────────────────────────
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        if st.button("❌ Record Failure", use_container_width=True):
            if breaker.state == CircuitState.OPEN:
                st.session_state.cb_log.append({"action": "BLOCKED (circuit open)", "state": breaker.state.value})
            else:
                breaker.record_failure()
                st.session_state.cb_log.append({"action": "FAILURE", "state": breaker.state.value})
            st.rerun()
    with c2:
        if st.button("✅ Record Success", use_container_width=True):
            if breaker.state == CircuitState.OPEN:
                st.session_state.cb_log.append({"action": "BLOCKED (circuit open)", "state": breaker.state.value})
            else:
                breaker.record_success()
                st.session_state.cb_log.append({"action": "SUCCESS", "state": breaker.state.value})
            st.rerun()
    with c3:
        if st.button("🔍 Probe (allow_request?)", use_container_width=True):
            result_allow = breaker.allow_request()
            st.session_state.cb_log.append({
                "action": f"PROBE → {'ALLOWED' if result_allow else 'BLOCKED'}",
                "state": breaker.state.value,
            })
            st.rerun()
    with c4:
        if st.button("🔄 Reset Breaker", use_container_width=True):
            st.session_state.cb_breaker = CircuitBreaker(
                "demo",
                failure_threshold=cfg.circuit_breaker.failure_threshold,
                reset_timeout_seconds=cfg.circuit_breaker.reset_timeout_seconds,
                success_threshold=cfg.circuit_breaker.success_threshold,
            )
            st.session_state.cb_log = []
            st.rerun()

    # ── transition log ───────────────────────────────────────────────────────
    st.divider()
    col_tl, col_act = st.columns([1, 1])

    with col_tl:
        st.subheader("Transition Log")
        if breaker.transition_log:
            for t in reversed(breaker.transition_log):
                color_from = COLORS.get(str(t["from"]), "#888")
                color_to = COLORS.get(str(t["to"]), "#888")
                st.markdown(
                    f"<span style='color:{color_from}'>{str(t['from']).upper()}</span> → "
                    f"<span style='color:{color_to}'>{str(t['to']).upper()}</span> "
                    f"<small style='color:#888'>({t['reason']})</small>",
                    unsafe_allow_html=True,
                )
        else:
            st.info("Chưa có transition nào.")

    with col_act:
        st.subheader("Action Log")
        for entry in reversed(st.session_state.cb_log[-15:]):
            action = entry["action"]
            icon = "✅" if "SUCCESS" in action else "❌" if "FAILURE" in action else "🔵" if "ALLOWED" in action else "⛔"
            st.markdown(f"{icon} `{action}` → **{entry['state']}**")

    # ── state machine diagram ────────────────────────────────────────────────
    st.divider()
    st.subheader("State Machine")

    # node positions (spread wide for clarity)
    _nodes = {
        "closed":    (0.5, 2.0, COLORS["closed"],    "CLOSED"),
        "open":      (3.5, 2.0, COLORS["open"],      "OPEN"),
        "half_open": (2.0, 0.4, COLORS["half_open"], "HALF_OPEN"),
    }
    _cur = breaker.state.value  # "closed" / "open" / "half_open"

    fig_sm = go.Figure()

    # ── glow ring for active node ──────────────────────────────────────────
    nx, ny, nc, _ = _nodes[_cur]
    fig_sm.add_trace(go.Scatter(
        x=[nx], y=[ny], mode="markers",
        marker=dict(size=110, color=nc, opacity=0.18, symbol="circle"),
        showlegend=False, hoverinfo="skip",
    ))

    # ── edges: arrow + midpoint label ─────────────────────────────────────
    _edges = [
        ("closed",    "open",      "≥ threshold\nfailures",  2.0,  2.25),
        ("open",      "half_open", "reset timeout\nelapsed",  3.05, 1.2),
        ("half_open", "closed",    "probe\nsuccess",          0.95, 1.2),
        ("half_open", "open",      "probe\nfailure",          3.0,  0.85),
    ]
    for src, dst, lbl, lx, ly in _edges:
        x0, y0 = _nodes[src][0], _nodes[src][1]
        x1, y1 = _nodes[dst][0], _nodes[dst][1]
        # arrow line
        fig_sm.add_annotation(
            x=x1, y=y1, ax=x0, ay=y0,
            xref="x", yref="y", axref="x", ayref="y",
            showarrow=True, arrowhead=3, arrowsize=1.4, arrowwidth=2,
            arrowcolor="#555",
            text="",
        )
        # label at explicit midpoint (away from nodes)
        fig_sm.add_annotation(
            x=lx, y=ly,
            xref="x", yref="y",
            showarrow=False,
            text=lbl.replace("\n", "<br>"),
            font=dict(size=10, color="#cccccc"),
            bgcolor="rgba(30,30,30,0.85)",
            bordercolor="#444",
            borderwidth=1,
            borderpad=4,
            align="center",
        )

    # ── nodes ─────────────────────────────────────────────────────────────
    for key, (nx2, ny2, nc2, label) in _nodes.items():
        is_cur = key == _cur
        # outer ring for active
        if is_cur:
            fig_sm.add_trace(go.Scatter(
                x=[nx2], y=[ny2], mode="markers",
                marker=dict(size=82, color="rgba(0,0,0,0)",
                            line=dict(width=3, color=nc2)),
                showlegend=False, hoverinfo="skip",
            ))
        fig_sm.add_trace(go.Scatter(
            x=[nx2], y=[ny2], mode="markers+text",
            marker=dict(
                size=68 if is_cur else 58,
                color=nc2,
                opacity=1.0 if is_cur else 0.28,
                line=dict(width=2, color="white" if is_cur else "#333"),
            ),
            text=[label],
            textposition="middle center",
            textfont=dict(color="white", size=12 if is_cur else 10,
                          family="monospace"),
            showlegend=False,
            hovertemplate=f"<b>{label}</b><extra></extra>",
        ))

    fig_sm.update_layout(
        height=340, showlegend=False,
        xaxis=dict(visible=False, range=[-0.2, 4.2]),
        yaxis=dict(visible=False, range=[-0.2, 2.9]),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        margin=dict(t=10, b=10, l=10, r=10),
    )
    st.plotly_chart(fig_sm, use_container_width=True)


# ═══════════════════════════════════════════════════════════════════════════
# PAGE 4 — CHAOS RUNNER
# ═══════════════════════════════════════════════════════════════════════════
elif page == "🔥 Chaos Runner":
    st.header("🔥 Chaos Runner")
    st.caption("Chọn scenario, chạy và xem kết quả metrics so sánh trực tiếp.")

    queries = load_queries()

    scenario_options = {s.name: s for s in cfg.scenarios}
    scenario_options["custom"] = None

    col_sel, col_cfg = st.columns([2, 1])

    with col_sel:
        selected = st.selectbox("Chọn scenario", list(scenario_options.keys()))

    with col_cfg:
        requests_override = st.slider("Requests", 10, 200,
                                      value=min(cfg.load_test.requests, 50),
                                      step=10)

    if selected == "custom":
        st.subheader("Custom Scenario")
        primary_fail = st.slider("Primary fail_rate", 0.0, 1.0, 0.5, 0.05)
        backup_fail = st.slider("Backup fail_rate", 0.0, 1.0, 0.0, 0.05)
        disable_cache = st.checkbox("Disable cache (for circuit testing)")
        custom_scenario = ScenarioConfig(
            name="custom",
            description="Custom scenario from UI",
            provider_overrides={"primary": primary_fail, "backup": backup_fail},
            disable_cache=disable_cache,
        )
        run_scenario_obj = custom_scenario
    else:
        run_scenario_obj = scenario_options[selected]
        sc = run_scenario_obj
        st.info(f"**{sc.name}**: {sc.description}  \n"
                f"provider_overrides: `{sc.provider_overrides}`  \n"
                f"disable_cache: `{sc.disable_cache}`")

    run_btn = st.button("▶️ Run Scenario", type="primary", use_container_width=False)

    if run_btn and run_scenario_obj:
        cfg_copy = load_config(str(CONFIG_PATH))
        cfg_copy.load_test.requests = requests_override

        with st.spinner(f"Running {run_scenario_obj.name} ({requests_override} requests)..."):
            t_start = time.perf_counter()
            result = run_scenario(cfg_copy, queries, run_scenario_obj)
            elapsed = time.perf_counter() - t_start

        st.success(f"Hoàn thành trong {elapsed:.1f}s")

        # ── result KPIs ───────────────────────────────────────────────────
        st.divider()
        k1, k2, k3, k4, k5, k6 = st.columns(6)
        k1.metric("Requests", result.total_requests)
        k2.metric("Availability", pct(result.availability))
        k3.metric("Error Rate", pct(result.error_rate))
        k4.metric("Cache Hit Rate", pct(result.cache_hit_rate))
        k5.metric("Circuit Opens", result.circuit_open_count)
        k6.metric("Recovery Time", ms(result.recovery_time_ms))

        # ── route breakdown pie ───────────────────────────────────────────
        st.divider()
        col_pie, col_lat2 = st.columns(2)

        with col_pie:
            st.subheader("Route Breakdown")
            labels = ["Primary", "Fallback", "Cache Hit", "Static Fallback"]
            values = [
                result.successful_requests - result.fallback_successes - result.cache_hits,
                result.fallback_successes,
                result.cache_hits,
                result.static_fallbacks,
            ]
            values = [max(0, v) for v in values]
            fig_pie = go.Figure(go.Pie(
                labels=labels, values=values,
                marker_colors=[COLORS["primary"], COLORS["fallback"],
                               COLORS["cache"], COLORS["static"]],
                hole=0.4,
            ))
            fig_pie.update_layout(height=300, margin=dict(t=0, b=0),
                                  paper_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig_pie, use_container_width=True)

        with col_lat2:
            st.subheader("Latency Distribution")
            if result.latencies_ms:
                import statistics
                sorted_lat = sorted(result.latencies_ms)
                fig_lat = go.Figure()
                fig_lat.add_trace(go.Histogram(
                    x=sorted_lat, nbinsx=30,
                    marker_color="#6366f1", opacity=0.8,
                    name="Latency",
                ))
                for label, val, color in [
                    ("P50", result.percentile(50), COLORS["closed"]),
                    ("P95", result.percentile(95), COLORS["half_open"]),
                    ("P99", result.percentile(99), COLORS["open"]),
                ]:
                    fig_lat.add_vline(x=val, line_color=color, line_width=2,
                                      annotation_text=f"{label}={val:.0f}ms",
                                      annotation_font_color=color)
                fig_lat.update_layout(height=300, margin=dict(t=10, b=10),
                                      xaxis_title="ms",
                                      plot_bgcolor="rgba(0,0,0,0)",
                                      paper_bgcolor="rgba(0,0,0,0)")
                st.plotly_chart(fig_lat, use_container_width=True)
