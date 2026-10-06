let page = "queue",
  selected = null,
  filter = "",
  query = "",
  offset = 0;
const tabs = [
  ["queue", "Exception queue"],
  ["imports", "Feed ingestion"],
  ["pricing", "Bond analytics"],
  ["audit", "Audit trail"],
];
async function render(next = page) {
  page = next;
  setNav(tabs, page, guarded(render));
  $("#main").innerHTML = '<div class="loading">Loading workspace…</div>';
  if (page === "queue") await queue();
  if (page === "imports") await imports();
  if (page === "pricing") await pricing();
  if (page === "audit") await audit();
}
function heading(title, description, action = "") {
  return `<div class="page-title"><div><h1>${title}</h1><p class="muted">${description}</p></div>${action}</div>`;
}
async function queue() {
  const [d, list] = await Promise.all([
    api("/dashboard"),
    api(
      `/cases?limit=20&offset=${offset}&q=${encodeURIComponent(query)}${filter ? "&status=" + filter : ""}`,
    ),
  ]);
  let states = d.states,
    total = Object.values(states).reduce((a, b) => a + b, 0);
  $("#main").innerHTML =
    heading(
      "Exception control",
      "Reconcile source records, investigate differences, and route decisions to an independent reviewer.",
      '<button id="refresh">Refresh queue</button>',
    ) +
    `<div class="stats">${stat("Open investigations", (states.pending || 0) + (states.working || 0) + (states.retry || 0), "Durable queue · bounded retries")}${stat("Awaiting review", states.awaiting_review || 0, "Independent reviewer required")}${stat("Closed", (states.approved || 0) + (states.rejected || 0), "Decision and rationale recorded")}${stat("Total cases", total, "Tenant: " + d.identity.tenant)}</div><div class="split"><section><div class="toolbar"><input id="search" aria-label="Search trades" placeholder="Trade ID or instrument" value="${esc(query)}"><select id="status" aria-label="Filter status"><option value="">All statuses</option>${["pending", "working", "awaiting_review", "approved", "rejected", "failed"].map((s) => `<option ${filter === s ? "selected" : ""} value="${s}">${s.replaceAll("_", " ")}</option>`).join("")}</select><button id="find">Search</button></div><div class="card flush"><div class="card-head row spread"><strong>Trade exceptions</strong><span class="muted small">${list.total} cases</span></div><div class="table-wrap"><table><thead><tr><th>Trade / instrument</th><th>Status</th><th>Cash difference</th></tr></thead><tbody>${list.items.map((r) => `<tr class="clickable" data-case="${r.id}"><td><strong class="mono">${esc(r.payload.internal.trade_id)}</strong><div class="small muted">${esc(r.payload.internal.instrument)} ${r.priority === "urgent" ? "· urgent" : ""}</div></td><td>${badge(r.status)}</td><td class="mono">${r.report?.cash_difference != null ? esc(fmt(r.report.cash_difference) + " " + r.report.currency) : "—"}</td></tr>`).join("")}</tbody></table>${!list.items.length ? empty("No cases match this view. Import a feed to get started.") : ""}</div><div class="card-head row spread"><button id="prev" ${offset === 0 ? "disabled" : ""}>Previous</button><span class="small muted">${list.total ? offset + 1 : 0}–${Math.min(offset + 20, list.total)} of ${list.total}</span><button id="next" ${offset + 20 >= list.total ? "disabled" : ""}>Next</button></div></div><footer>Amounts use currency per unit. Incomparable currencies and instruments have no cash-difference estimate.</footer></section><section id="detail" class="card">${empty("Select an exception to inspect its evidence and history.")}</section></div>`;
  $("#refresh").onclick = guarded(() => queue());
  $("#find").onclick = guarded(() => {
    query = $("#search").value;
    filter = $("#status").value;
    offset = 0;
    return queue();
  });
  $("#search").onkeydown = (e) => {
    if (e.key === "Enter") $("#find").click();
  };
  $("#prev").onclick = guarded(() => {
    offset = Math.max(0, offset - 20);
    return queue();
  });
  $("#next").onclick = guarded(() => {
    offset += 20;
    return queue();
  });
  document
    .querySelectorAll("[data-case]")
    .forEach((el) => (el.onclick = guarded(() => detail(el.dataset.case))));
  if (selected) await detail(selected);
}
async function detail(id) {
  selected = id;
  const [r, history] = await Promise.all([
    api("/cases/" + id),
    api("/cases/" + id + "/events"),
  ]);
  let a = r.payload.internal,
    b = r.payload.counterparty,
    closed = ["approved", "rejected"].includes(r.status);
  $("#detail").innerHTML =
    `<div class="row spread"><div><span class="muted small">INVESTIGATION</span><h2 class="mono">${esc(a.trade_id)}</h2></div>${badge(r.status)}</div><div class="table-wrap"><table><thead><tr><th>Field</th><th>Booking</th><th>Confirmation</th></tr></thead><tbody>${["instrument", "quantity", "price", "settlement", "currency"].map((k) => `<tr><td>${k}</td><td class="mono">${esc(a[k])}</td><td class="mono">${a[k] !== b[k] ? '<span class="badge warn">' + esc(b[k]) + "</span>" : esc(b[k])}</td></tr>`).join("")}</tbody></table></div>${r.report ? `<div class="notice"><strong>${esc(r.report.summary)}</strong><div class="small">${esc(r.report.narrative_source)} · ${esc(r.report.severity)} severity</div></div>${r.report.policy_evidence?.length ? "<h3>Policy evidence</h3>" + r.report.policy_evidence.map((c) => `<div class="quote">${esc(c.text)}<div class="small muted">${esc(c.id)}</div></div>`).join("") : ""}` : '<p class="muted small">The evidence report will appear after investigation.</p>'}<div class="row"><span class="pill">Attempt ${r.attempts} / 3</span><span class="pill">Version ${r.version}</span><span class="pill">Maker: ${esc(r.maker)}</span>${r.assignee ? '<span class="pill">Assigned: ' + esc(r.assignee) + "</span>" : ""}</div>${["pending", "retry"].includes(r.status) ? '<button class="primary" id="investigate">Run investigation</button>' : ""}${!closed ? `<h3>Case note</h3><textarea id="note" aria-label="Case note" placeholder="Record the evidence behind your update or decision…" style="min-height:85px"></textarea><div class="row"><input id="assignee" aria-label="Assignee" placeholder="Assignee" value="${esc(r.assignee || "")}"><select id="priority" aria-label="Priority"><option value="normal">Normal</option><option value="urgent" ${r.priority === "urgent" ? "selected" : ""}>Urgent</option></select><button id="save-note">Save note</button></div>${r.status === "awaiting_review" ? '<div class="row" style="margin-top:14px"><button class="primary" id="approve">Approve case</button><button class="danger" id="reject">Reject case</button><span class="small muted">Reviewer identity required.</span></div>' : ""}${r.status === "failed" ? '<button id="retry">Retry failed investigation</button>' : ""}` : ""}<h3>Activity</h3><div class="timeline">${history.events
      .slice()
      .reverse()
      .map(
        ({ event }) =>
          `<div class="event"><strong class="small">${esc(event.kind.replaceAll("_", " "))}</strong><div class="small muted">${esc(event.actor)} · ${when(event.at)}</div>${event.detail.note ? "<p>" + esc(event.detail.note) + "</p>" : ""}</div>`,
      )
      .join("")}</div>`;
  if ($("#investigate"))
    $("#investigate").onclick = guarded(async () => {
      $("#investigate").disabled = true;
      await post("/cases/" + id + "/investigate", {});
      toast("Investigation complete");
      await queue();
    });
  const note = () => {
    let value = $("#note").value.trim();
    if (value.length < 5)
      throw Error("Add a note of at least five characters.");
    return value;
  };
  if ($("#save-note"))
    $("#save-note").onclick = guarded(async () => {
      await api("/cases/" + id, {
        method: "PATCH",
        body: JSON.stringify({
          expected_version: r.version,
          note: note(),
          assignee: $("#assignee").value,
          priority: $("#priority").value,
        }),
      });
      toast("Case updated");
      await queue();
    });
  for (let action of ["approve", "reject"])
    if ($("#" + action))
      $("#" + action).onclick = guarded(async () => {
        await post("/cases/" + id + "/decision", {
          approved: action === "approve",
          note: note(),
          expected_version: r.version,
        });
        toast("Decision recorded");
        await queue();
      });
  if ($("#retry"))
    $("#retry").onclick = guarded(async () => {
      await post("/cases/" + id + "/retry", {
        expected_version: r.version,
        note: note(),
      });
      await queue();
    });
}
async function imports() {
  let history = await api("/imports");
  $("#main").innerHTML =
    heading(
      "From feeds to investigations.",
      "Validate both files, reconcile by trade ID, and quarantine records without a matching source.",
      '<button id="example">Load example feeds</button>',
    ) +
    `<div class="grid two"><section class="card"><h2>Import a pair of feeds</h2><label for="internal">Internal booking CSV</label><textarea id="internal" placeholder="trade_id,instrument,quantity,price,settlement,currency"></textarea><label for="counterparty">Counterparty confirmation CSV</label><textarea id="counterparty"></textarea><div class="row" style="margin-top:15px"><button class="primary" id="import">Validate and import</button><span class="small muted">Up to 5,000 rows per feed</span></div><div id="import-result"></div></section><section class="card"><h2>Import history</h2>${history.items.length ? history.items.map((r) => `<details><summary>${when(r.created_at)} · ${r.cases.length} exceptions · ${r.matched} matched</summary><p class="small">${r.quarantined.length} unmatched records quarantined.</p>${r.quarantined.length ? jsonView(r.quarantined) : ""}</details>`).join("") : empty("No imports for this tenant yet.")}<div class="notice">Validation completes before writing. Import retries replay the same result; repeated record pairs reuse their existing cases.</div></section></div>`;
  $("#example").onclick = guarded(async () => {
    let d = await api("/examples/feeds");
    $("#internal").value = d.internal_csv;
    $("#counterparty").value = d.counterparty_csv;
  });
  let importKey = crypto.randomUUID();
  $("#import").onclick = guarded(async () => {
    const data = await post(
      "/imports",
      {
        internal_csv: $("#internal").value,
        counterparty_csv: $("#counterparty").value,
      },
      { "Idempotency-Key": importKey },
    );
    $("#import-result").innerHTML =
      `<div class="notice">${data.cases.filter((c) => c.created).length} new cases · ${data.matched} matched · ${data.quarantined.length} quarantined</div>${data.quarantined.length ? jsonView(data.quarantined) : ""}`;
    toast("Feed import completed");
    importKey = crypto.randomUUID();
  });
}
async function pricing() {
  $("#main").innerHTML =
    heading(
      "Trace every cash flow.",
      "Inspect clean and dirty prices, accrued interest, duration and yield sensitivity.",
    ) +
    `<div class="split"><section class="card"><h2>Fixed-coupon bond</h2><div class="form-grid">${[
      ["settlement", "Settlement", "2026-11-10", "date"],
      ["maturity", "Maturity", "2031-11-30", "date"],
      ["coupon_rate", "Coupon rate (%)", "4.5", "number"],
      ["yield_rate", "Yield (%)", "4.8", "number"],
      ["face", "Face amount", "100", "number"],
    ]
      .map(
        ([id, label, value, type]) =>
          `<div><label for="${id}">${label}</label><input id="${id}" type="${type}" step="any" value="${value}"></div>`,
      )
      .join(
        "",
      )}<div><label for="frequency">Coupon frequency</label><select id="frequency"><option value="2">Semiannual</option><option value="1">Annual</option><option value="4">Quarterly</option></select></div></div><button class="primary" id="calculate" style="margin-top:20px">Calculate cash flows</button><p class="small muted" style="margin-top:18px">Regular coupons; maturity-anchored end-of-month dates. ACT/365F discounting with nominal periodic yield and ACT/ACT coupon accrual. No holiday adjustment or irregular stubs.</p></section><section id="pricing-result" class="card"></section></div>`;
  $("#calculate").onclick = guarded(async () => {
    let d = await post("/pricing/fixed-bond", {
      settlement: $("#settlement").value,
      maturity: $("#maturity").value,
      coupon_rate: +$("#coupon_rate").value / 100,
      yield_rate: +$("#yield_rate").value / 100,
      frequency: +$("#frequency").value,
      face: +$("#face").value,
    });
    let values = d.shocks.map((s) => s.clean_price),
      min = Math.min(...values),
      max = Math.max(...values),
      points = values
        .map(
          (v, i) =>
            `${35 + i * 68},${160 - ((v - min) / (max - min || 1)) * 120}`,
        )
        .join(" ");
    $("#pricing-result").innerHTML =
      `<h2>Pricing results</h2><div class="grid two">${stat("Clean price", fmt(d.clean_price, 4))}${stat("Dirty price", fmt(d.dirty_price, 4))}${stat("DV01", fmt(d.dv01, 5), "Value change per basis point")}${stat("Modified duration", fmt(d.modified_duration, 3), "Years")}</div><h3>Clean price under yield shocks</h3><svg viewBox="0 0 490 205" role="img" aria-label="Bond clean price declines as yield rises"><path d="M35 20V160H450" fill="none" stroke="#cbd8de"/><polyline points="${points}" fill="none" stroke="#147a78" stroke-width="3"/>${d.shocks.map((s, i) => `<text x="${35 + i * 68}" y="185" text-anchor="middle" font-size="10" fill="#657a89">${s.basis_points}bp</text>`).join("")}<text x="35" y="15" font-size="11" fill="#657a89">${fmt(max, 2)}</text><text x="440" y="150" text-anchor="end" font-size="11" fill="#657a89">${fmt(min, 2)}</text></svg><p class="small muted">Accrued: ${fmt(d.accrued_interest, 4)} · Convexity: ${fmt(d.convexity, 3)}</p><details><summary>${d.cashflows.length} dated cash flows</summary><div class="table-wrap"><table><thead><tr><th>Payment</th><th>Coupon</th><th>Principal</th><th>PV</th></tr></thead><tbody>${d.cashflows.map((c) => `<tr><td>${c.date}</td><td>${fmt(c.coupon, 4)}</td><td>${fmt(c.principal)}</td><td>${fmt(c.present_value, 4)}</td></tr>`).join("")}</tbody></table></div></details>`;
  });
  $("#calculate").click();
}
async function audit() {
  try {
    let d = await api("/audit");
    $("#main").innerHTML =
      heading(
        "An inspectable decision history.",
        "Every transition records an actor, timestamp, and hash linked to the prior event.",
        '<button id="export">Export audit JSON</button>',
      ) +
      `<div class="stats">${stat("Chain verification", d.valid ? "Valid" : "Invalid")}${stat("Recorded events", d.events.length)}${stat("Latest sequence", d.events.at(-1)?.sequence || 0)}${stat("Storage", "SQLite WAL", "Single-host transactional store")}</div><div class="card"><h2>Current chain head</h2><p class="mono small" style="overflow-wrap:anywhere">${esc(d.head)}</p><p class="muted small">This detects edits against the stored chain. Detecting a complete rewrite or truncation requires an independently retained checkpoint.</p></div><div class="card flush" style="margin-top:20px"><div class="table-wrap"><table><thead><tr><th>Sequence</th><th>Event</th><th>Actor</th><th>Time</th><th>Case</th></tr></thead><tbody>${d.events
        .slice()
        .reverse()
        .map((r) => {
          let e = JSON.parse(r.body);
          return `<tr><td>${r.sequence}</td><td>${badge(e.kind)}</td><td>${esc(e.actor)}</td><td>${when(e.at)}</td><td class="mono small">${esc(e.case_id.slice(0, 12))}</td></tr>`;
        })
        .join("")}</tbody></table></div></div>`;
    $("#export").onclick = () => {
      let u = URL.createObjectURL(
          new Blob([JSON.stringify(d, null, 2)], { type: "application/json" }),
        ),
        a = document.createElement("a");
      a.href = u;
      a.download = "trade-desk-audit.json";
      a.click();
      URL.revokeObjectURL(u);
    };
  } catch (e) {
    $("#main").innerHTML =
      heading(
        "Audit trail",
        "Reviewers can inspect and export their tenant’s audit chain.",
      ) +
      '<div class="notice">' +
      esc(e.message) +
      ". Select the reviewer identity to continue.</div>";
  }
}
window.addEventListener(
  "identity-changed",
  guarded(() => {
    selected = null;
    offset = 0;
    return render();
  }),
);
guarded(async () => {
  await initAuth();
  await render();
})();
