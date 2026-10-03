import m from "mithril";

const TOKEN_KEY = "bridge_strain_token";
const USER_KEY = "bridge_strain_user";

function verdictClass(verdict, status) {
  if (verdict === "合格") return "tag pass";
  if (verdict === "越界") return "tag fail";
  return "tag wait";
}

function displayVerdict(row) {
  if (row.verdict) return row.verdict;
  if (row.status === "pending") return "待处理";
  if (row.status === "processing") return "处理中";
  return "—";
}

function fmtNum(v) {
  if (v === null || v === undefined) return "—";
  return Number(v).toString();
}

const state = {
  token: localStorage.getItem(TOKEN_KEY) || "",
  user: null,
  page: "readings",
  loginForm: { username: "surveyor", password: "surv123456" },
  submitForm: { span_code: "", microstrain: "", site_temp: "" },
  configForm: { span_code: "", coeff: "0.1", base_temp: "" },
  rows: [],
  configs: [],
  ledger: [],
  amend: null, // { id, value }
  error: "",
  msg: "",
  loading: false,
  timer: null,
};

try {
  state.user = JSON.parse(localStorage.getItem(USER_KEY) || "null");
} catch {
  state.user = null;
}

async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  const res = await fetch(path, { ...opts, headers });
  const text = await res.text();
  let data = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    data = { detail: text };
  }
  if (!res.ok) throw new Error(data.detail || res.statusText);
  return data;
}

async function loadReadings() {
  if (!state.token) return;
  try {
    state.rows = await api("/api/readings");
    state.error = "";
  } catch {
    state.error = "加载列表失败，请重新登录";
  }
  m.redraw();
}

async function loadCompensation() {
  if (!state.token) return;
  try {
    const [configs, ledger] = await Promise.all([
      api("/api/compensation/config"),
      api("/api/ledger"),
    ]);
    state.configs = configs;
    state.ledger = ledger;
  } catch {
    /* 主列表轮询已负责登录失效提示 */
  }
  m.redraw();
}

function pollTick() {
  loadReadings();
  if (state.page === "compensation") loadCompensation();
}

function startPolling() {
  if (state.timer) clearInterval(state.timer);
  if (!state.token) return;
  state.timer = setInterval(pollTick, 3000);
}

function logout() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
  state.token = "";
  state.user = null;
  state.rows = [];
  state.configs = [];
  state.ledger = [];
  if (state.timer) clearInterval(state.timer);
}

function compensatedPreview() {
  const cfg = state.configs.find(
    (c) => c.span_code === state.submitForm.span_code.trim()
  );
  const raw = parseFloat(state.submitForm.microstrain);
  const site = parseFloat(state.submitForm.site_temp);
  if (!cfg || Number.isNaN(raw) || Number.isNaN(site)) return null;
  return {
    coeff: cfg.coeff,
    base: cfg.base_temp,
    value: raw + cfg.coeff * (site - cfg.base_temp),
  };
}

const LoginPage = {
  view: () =>
    m("div.wrap", [
      m("h1", "桥梁应变班交台"),
      m(
        "p.sub",
        "测量员提交跨段编号与微应变读数，服务端先做温度补偿再判定合格或越界。"
      ),
      m("div.card", [
        m(
          "form",
          {
            onsubmit: async (e) => {
              e.preventDefault();
              state.error = "";
              state.loading = true;
              try {
                const data = await api("/api/auth/login", {
                  method: "POST",
                  body: JSON.stringify(state.loginForm),
                });
                state.token = data.access_token;
                state.user = { username: data.username, role: data.role };
                localStorage.setItem(TOKEN_KEY, state.token);
                localStorage.setItem(USER_KEY, JSON.stringify(state.user));
                await loadReadings();
                startPolling();
              } catch {
                state.error = "用户名或密码错误";
              } finally {
                state.loading = false;
                m.redraw();
              }
            },
          },
          [
            m("div.row", [
              m("label", [
                "用户名",
                m("input", {
                  value: state.loginForm.username,
                  oninput: (e) => {
                    state.loginForm.username = e.target.value;
                  },
                }),
              ]),
              m("label", [
                "密码",
                m("input", {
                  type: "password",
                  value: state.loginForm.password,
                  oninput: (e) => {
                    state.loginForm.password = e.target.value;
                  },
                }),
              ]),
              m("button", { type: "submit", disabled: state.loading }, "登录"),
            ]),
            state.error ? m("p.err", state.error) : null,
          ]
        ),
        m(
          "p.sub",
          { style: { marginBottom: 0 } },
          "测量员 surveyor / surv123456 · 复核员 reviewer / rev123456"
        ),
      ]),
    ]),
};

function topbar(isWriter) {
  const tab = (key, label) =>
    m(
      "button.secondary.tab" + (state.page === key ? ".active" : ""),
      {
        type: "button",
        onclick: () => {
          state.page = key;
          state.error = "";
          state.msg = "";
          if (key === "compensation") loadCompensation();
        },
      },
      label
    );
  return m("div.topbar", [
    m("div", [
      m("h1", "桥梁应变班交台"),
      m("div.tabs", [tab("readings", "读数台账"), tab("compensation", "温度补偿")]),
    ]),
    m("div", [
      `${state.user?.username}（${isWriter ? "测量员" : "复核员"}） `,
      m("button.secondary", { type: "button", onclick: logout }, "退出"),
    ]),
  ]);
}

function configSection(isWriter) {
  return m("div.card", [
    m("h2", { style: { marginTop: 0, fontSize: "1.1rem" } }, [
      "补偿系数设置",
      m(
        "span.note",
        isWriter
          ? "补偿系数允许范围 0～0.5；每跨段一个系数与一个基准气温。"
          : "复核员只读，不能修改系数。"
      ),
    ]),
    isWriter
      ? m(
          "form",
          {
            onsubmit: async (e) => {
              e.preventDefault();
              state.error = "";
              state.msg = "";
              state.loading = true;
              try {
                const data = await api("/api/compensation/config", {
                  method: "PUT",
                  body: JSON.stringify({
                    span_code: state.configForm.span_code,
                    coeff: state.configForm.coeff,
                    base_temp: state.configForm.base_temp,
                  }),
                });
                state.msg = data.message || "已保存";
                state.configForm = {
                  ...state.configForm,
                  span_code: data.span_code,
                };
                await loadCompensation();
              } catch (err) {
                state.error = err.message || "保存失败";
              } finally {
                state.loading = false;
                m.redraw();
              }
            },
          },
          [
            m("div.row", [
              m("label", [
                "跨段编号",
                m("input", {
                  placeholder: "例如 跨中S1",
                  value: state.configForm.span_code,
                  oninput: (e) => {
                    state.configForm.span_code = e.target.value;
                  },
                }),
              ]),
              m("label", [
                "补偿系数",
                m("input", {
                  type: "number",
                  step: "0.01",
                  min: "0",
                  max: "0.5",
                  placeholder: "0～0.5",
                  value: state.configForm.coeff,
                  oninput: (e) => {
                    state.configForm.coeff = e.target.value;
                  },
                }),
              ]),
              m("label", [
                "基准气温（℃）",
                m("input", {
                  type: "number",
                  step: "0.1",
                  value: state.configForm.base_temp,
                  oninput: (e) => {
                    state.configForm.base_temp = e.target.value;
                  },
                }),
              ]),
              m("button", { type: "submit", disabled: state.loading }, "保存系数"),
            ]),
          ]
        )
      : null,
    m("table", { style: { marginTop: "0.75rem" } }, [
      m("thead", [
        m("tr", [
          m("th", "跨段"),
          m("th", "补偿系数"),
          m("th", "基准气温（℃）"),
          m("th", "更新人"),
          m("th", "更新时间"),
        ]),
      ]),
      m(
        "tbody",
        state.configs.length
          ? state.configs.map((c) =>
              m("tr", { key: c.span_code }, [
                m("td", c.span_code),
                m("td", fmtNum(c.coeff)),
                m("td", fmtNum(c.base_temp)),
                m("td", c.updated_by),
                m("td", c.updated_at ? c.updated_at.replace("T", " ").slice(0, 19) : "—"),
              ])
            )
          : [m("tr", m("td", { colspan: "5" }, "尚未设置任何跨段补偿"))]
      ),
    ]),
  ]);
}

function submitSection() {
  const preview = compensatedPreview();
  return m("div.card", [
    m("h2", { style: { marginTop: 0, fontSize: "1.1rem" } }, [
      "报送读数",
      m(
        "span.note",
        "微应变判定前先吃温度补偿：补偿后 = 原文 + 系数 ×（现场气温 − 基准气温）。须填现场气温。"
      ),
    ]),
    m(
      "form",
      {
        onsubmit: async (e) => {
          e.preventDefault();
          state.error = "";
          state.msg = "";
          state.loading = true;
          try {
            const data = await api("/api/readings", {
              method: "POST",
              // 直接把原始输入交给服务端校验，保证网页与直连 API 的退回说法一致
              body: JSON.stringify({
                span_code: state.submitForm.span_code,
                microstrain: state.submitForm.microstrain,
                site_temp: state.submitForm.site_temp,
              }),
            });
            state.msg = data.message || "已提交";
            state.submitForm = { span_code: "", microstrain: "", site_temp: "" };
            await Promise.all([loadReadings(), loadCompensation()]);
          } catch (err) {
            state.error = err.message || "提交失败";
          } finally {
            state.loading = false;
            m.redraw();
          }
        },
      },
      [
        m("div.row", [
          m("label", [
            "跨段编号",
            m("input", {
              placeholder: "例如 跨中S1",
              value: state.submitForm.span_code,
              oninput: (e) => {
                state.submitForm.span_code = e.target.value;
              },
            }),
          ]),
          m("label", [
            "原始微应变（με）",
            m("input", {
              type: "number",
              step: "0.1",
              value: state.submitForm.microstrain,
              oninput: (e) => {
                state.submitForm.microstrain = e.target.value;
              },
            }),
          ]),
          m("label", [
            "现场气温（℃）",
            m("input", {
              type: "number",
              step: "0.1",
              value: state.submitForm.site_temp,
              oninput: (e) => {
                state.submitForm.site_temp = e.target.value;
              },
            }),
          ]),
          m("button", { type: "submit", disabled: state.loading }, "报送"),
        ]),
        preview
          ? m(
              "p.note",
              `补偿预览：${parseFloat(state.submitForm.microstrain)} + ${preview.coeff} × (${parseFloat(state.submitForm.site_temp)} − ${preview.base}) = ${preview.value.toFixed(2).replace(/\.?0+$/, "")} με（最终以服务端计算为准）`
            )
          : null,
      ]
    ),
  ]);
}

function reconcileCell(entry) {
  const map = {
    match: ["tag pass", "对拍一致"],
    drift: ["tag fail", "账本漂移"],
    pending: ["tag wait", "待处理"],
    missing: ["tag fail", "在线单缺失"],
  };
  const [cls, label] = map[entry.reconcile_state] || ["tag wait", entry.reconcile_state];
  return m("td", [
    m("span", { class: cls }, label),
    entry.diffs && entry.diffs.length
      ? m(
          "ul.diffs",
          entry.diffs.map((d) => m("li", d))
        )
      : null,
  ]);
}

function ledgerSection() {
  return m("div.card", [
    m("h2", { style: { marginTop: 0, fontSize: "1.1rem" } }, [
      "补偿账本",
      m(
        "span.note",
        "追加式账本，只记不改不删；在线单事后被改数字时，账本旧值不动，此处对拍标红。"
      ),
    ]),
    m("table", [
      m("thead", [
        m("tr", [
          m("th", "账本号"),
          m("th", "单据号"),
          m("th", "跨段"),
          m("th", "原文（με）"),
          m("th", "系数"),
          m("th", "基准气温"),
          m("th", "现场气温"),
          m("th", "补偿后（με）"),
          m("th", "账本结论"),
          m("th", "在线对拍"),
          m("th", "记账人"),
        ]),
      ]),
      m(
        "tbody",
        state.ledger.length
          ? state.ledger.map((l) =>
              m("tr", { key: l.ledger_id, class: l.reconcile_state === "drift" ? "drift-row" : "" }, [
                m("td", l.ledger_id),
                m("td", l.reading_id),
                m("td", l.span_code),
                m("td", fmtNum(l.raw_microstrain)),
                m("td", fmtNum(l.coeff)),
                m("td", `${fmtNum(l.base_temp)} ℃`),
                m("td", `${fmtNum(l.site_temp)} ℃`),
                m("td", fmtNum(l.compensated_microstrain)),
                m("td", [m("span", { class: verdictClass(l.verdict, "done") }, l.verdict)]),
                reconcileCell(l),
                m("td", l.ledger_by),
              ])
            )
          : [m("tr", m("td", { colspan: "11" }, "账本为空，报送读数后自动落笔"))]
      ),
    ]),
  ]);
}

function compensationPage(isWriter) {
  return m("div.wrap", [
    topbar(isWriter),
    configSection(isWriter),
    isWriter ? submitSection() : null,
    ledgerSection(),
    state.error ? m("p.err", state.error) : null,
    state.msg ? m("p.ok", state.msg) : null,
  ]);
}

async function saveAmend(row) {
  state.error = "";
  try {
    await api(`/api/readings/${row.id}/amend`, {
      method: "POST",
      body: JSON.stringify({ microstrain: state.amend.value }),
    });
    state.amend = null;
    await Promise.all([loadReadings(), loadCompensation()]);
  } catch (err) {
    state.error = err.message || "修订失败";
  }
  m.redraw();
}

function readingsPage(isWriter) {
  return m("div.wrap", [
    topbar(isWriter),
    m("div.card", [
      m("h2", { style: { marginTop: 0, fontSize: "1.1rem" } }, [
        "读数列表",
        m("span.note", "判定一律使用温度补偿后读数。"),
      ]),
      m("table", [
        m("thead", [
          m("tr", [
            m("th", "编号"),
            m("th", "跨段"),
            m("th", "原文（με）"),
            m("th", "现场气温"),
            m("th", "补偿后（με）"),
            m("th", "结论"),
            m("th", "说明"),
            m("th", "状态"),
            m("th", "提交人"),
            isWriter ? m("th", "操作") : null,
          ]),
        ]),
        m(
          "tbody",
          state.rows.length
            ? state.rows.map((r) =>
                m("tr", { key: r.id, class: r.amended ? "amended-row" : "" }, [
                  m("td", r.id),
                  m("td", r.span_code),
                  m("td", fmtNum(r.microstrain)),
                  m("td", r.site_temp === null || r.site_temp === undefined ? "—" : `${fmtNum(r.site_temp)} ℃`),
                  m("td", fmtNum(r.compensated_microstrain)),
                  m("td", [
                    m("span", { class: verdictClass(r.verdict, r.status) }, displayVerdict(r)),
                  ]),
                  m("td", r.reason || "—"),
                  m("td", [
                    r.status,
                    r.amended ? m("span.tag.wait", { style: { marginLeft: "0.35rem" } }, "已修订") : null,
                  ]),
                  m("td", r.created_by),
                  isWriter
                    ? m(
                        "td",
                        r.status === "done" && r.coeff !== null && r.coeff !== undefined
                          ? state.amend && state.amend.id === r.id
                            ? m("div.amendbox", [
                                m("input", {
                                  type: "number",
                                  step: "0.1",
                                  value: state.amend.value,
                                  oninput: (e) => {
                                    state.amend.value = e.target.value;
                                  },
                                }),
                                m(
                                  "button.mini",
                                  { type: "button", onclick: () => saveAmend(r) },
                                  "确认"
                                ),
                                m(
                                  "button.secondary.mini",
                                  {
                                    type: "button",
                                    onclick: () => {
                                      state.amend = null;
                                      m.redraw();
                                    },
                                  },
                                  "取消"
                                ),
                              ])
                            : m(
                                "button.secondary.mini",
                                {
                                  type: "button",
                                  onclick: () => {
                                    state.amend = {
                                      id: r.id,
                                      value: String(r.microstrain),
                                    };
                                    state.error = "";
                                    m.redraw();
                                  },
                                },
                                "改单"
                              )
                          : "—"
                      )
                    : null,
                ])
              )
            : [m("tr", m("td", { colspan: isWriter ? 10 : 9 }, "暂无数据"))]
        ),
      ]),
      state.error ? m("p.err", state.error) : null,
    ]),
  ]);
}

const App = {
  oninit() {
    loadReadings();
    startPolling();
  },
  onremove() {
    if (state.timer) clearInterval(state.timer);
  },
  view() {
    if (!state.token) return m(LoginPage);
    const isWriter = state.user?.role === "writer";
    return state.page === "compensation"
      ? compensationPage(isWriter)
      : readingsPage(isWriter);
  },
};

export default App;
