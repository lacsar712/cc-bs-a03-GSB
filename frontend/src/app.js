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

function fmt(value, digits = 2) {
  if (value === null || value === undefined) return "—";
  return Number(value).toFixed(digits);
}

const state = {
  token: localStorage.getItem(TOKEN_KEY) || "",
  user: null,
  page: "list",
  loginForm: { username: "surveyor", password: "surv123456" },
  submitForm: { span_code: "", microstrain: "", field_temperature: "" },
  compForm: { span_code: "", coefficient: "", base_temperature: "" },
  rows: [],
  compensations: [],
  ledger: [],
  error: "",
  msg: "",
  compError: "",
  compMsg: "",
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

async function loadAll() {
  if (!state.token) return;
  try {
    const [readings, compensations, ledger] = await Promise.all([
      api("/api/readings"),
      api("/api/compensation"),
      api("/api/ledger"),
    ]);
    state.rows = readings;
    state.compensations = compensations;
    state.ledger = ledger;
    state.error = "";
  } catch {
    state.error = "加载数据失败，请重新登录";
  }
  m.redraw();
}

function startPolling() {
  if (state.timer) clearInterval(state.timer);
  if (!state.token) return;
  state.timer = setInterval(loadAll, 3000);
}

function logout() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
  state.token = "";
  state.user = null;
  state.rows = [];
  state.compensations = [];
  state.ledger = [];
  if (state.timer) clearInterval(state.timer);
}

const LoginView = {
  view: () =>
    m("div.wrap", [
      m("h1", "桥梁应变班交台"),
      m(
        "p.sub",
        "测量员提交跨段编号与现场微应变读数，服务端先做温度补偿再判定合格或越界。"
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
                await loadAll();
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
  return m("div.topbar", [
    m("div", [
      m("h1", "桥梁应变班交台"),
      m("p.sub", "微应变 80～220 με 为合格；判定一律使用温度补偿后读数。"),
    ]),
    m("div.topright", [
      m("div.nav", [
        m(
          "button.secondary" + (state.page === "list" ? ".active" : ""),
          {
            type: "button",
            onclick: () => {
              state.page = "list";
            },
          },
          "读数列表"
        ),
        m(
          "button.secondary" + (state.page === "comp" ? ".active" : ""),
          {
            type: "button",
            onclick: () => {
              state.page = "comp";
            },
          },
          "温度补偿"
        ),
      ]),
      m("div.userline", [
        `${state.user?.username}（${isWriter ? "测量员" : "复核员"}） `,
        m(
          "button.secondary",
          {
            type: "button",
            onclick: () => {
              logout();
              m.redraw();
            },
          },
          "退出"
        ),
      ]),
    ]),
  ]);
}

const ReadingListView = {
  view: () =>
    m("div.card", [
      m("h2", { style: { marginTop: 0, fontSize: "1.1rem" } }, "读数列表"),
      m("table", [
        m("thead", [
          m("tr", [
            m("th", "编号"),
            m("th", "跨段"),
            m("th", "原文读数"),
            m("th", "现场气温"),
            m("th", "补偿后读数"),
            m("th", "结论"),
            m("th", "说明"),
            m("th", "状态"),
            m("th", "账本"),
            m("th", "提交人"),
          ]),
        ]),
        m(
          "tbody",
          state.rows.length
            ? state.rows.map((r) =>
                m("tr", { key: r.id }, [
                  m("td", r.id),
                  m("td", r.span_code),
                  m("td", `${fmt(r.microstrain)} με`),
                  m("td", r.field_temperature === null ? "—" : `${fmt(r.field_temperature, 1)}℃`),
                  m("td", r.compensated_microstrain === null ? "—" : `${fmt(r.compensated_microstrain)} με`),
                  m("td", [
                    m("span", { class: verdictClass(r.verdict, r.status) }, displayVerdict(r)),
                  ]),
                  m("td", r.reason || "—"),
                  m("td", r.status === "done" ? "已处理" : r.status === "pending" ? "待处理" : "处理中"),
                  m("td", r.in_ledger ? m("span.tag.pass", "已落笔") : m("span.tag.wait", "无")),
                  m("td", r.created_by),
                ])
              )
            : [m("tr", m("td", { colspan: 10 }, "暂无数据"))]
        ),
      ]),
    ]),
};

const CompensationPage = {
  view: () => {
    const isWriter = state.user?.role === "writer";
    return [
      // 系数设置：测量员可改，复核员只读。
      m("div.card", [
        m("h2", { style: { marginTop: 0, fontSize: "1.1rem" } }, [
          "温度补偿系数设置",
          m(
            "span.hint",
            isWriter ? "（补偿后读数 = 原文 + 系数 ×（现场气温 − 基准气温），系数允许 0～1）" : "（复核员只读）"
          ),
        ]),
        m("table", [
          m("thead", [
            m("tr", [
              m("th", "跨段"),
              m("th", "补偿系数"),
              m("th", "基准气温"),
              m("th", "最后更新人"),
              m("th", "更新时间"),
            ]),
          ]),
          m(
            "tbody",
            state.compensations.length
              ? state.compensations.map((c) =>
                  m("tr", { key: c.span_code }, [
                    m("td", c.span_code),
                    m("td", fmt(c.coefficient, 2)),
                    m("td", `${fmt(c.base_temperature, 1)}℃`),
                    m("td", c.updated_by),
                    m("td", c.updated_at ? c.updated_at.replace("T", " ").slice(0, 19) : "—"),
                  ])
                )
              : [m("tr", m("td", { colspan: 5 }, "尚未设置任何跨段"))]
          ),
        ]),
        isWriter
          ? m(
              "form.compform",
              {
                onsubmit: async (e) => {
                  e.preventDefault();
                  state.compError = "";
                  state.compMsg = "";
                  // 前端不做范围拦截：缺项/越界一律由服务端退回并原样展示说法，
                  // 与绕过网页的请求保持一致。
                  try {
                    const data = await api("/api/compensation", {
                      method: "POST",
                      body: JSON.stringify({
                        span_code: state.compForm.span_code,
                        coefficient: parseFloat(state.compForm.coefficient),
                        base_temperature: parseFloat(state.compForm.base_temperature),
                      }),
                    });
                    state.compMsg = data.message || "温度补偿设置已保存";
                    state.compForm = { span_code: "", coefficient: "", base_temperature: "" };
                    await loadAll();
                  } catch (err) {
                    state.compError = err.message || "保存失败";
                  }
                  m.redraw();
                },
              },
              [
                m("div.row", [
                  m("label", [
                    "跨段编号",
                    m("input", {
                      placeholder: "例如 跨中S3",
                      value: state.compForm.span_code,
                      oninput: (e) => {
                        state.compForm.span_code = e.target.value;
                      },
                    }),
                  ]),
                  m("label", [
                    "补偿系数（0～1）",
                    m("input", {
                      type: "number",
                      step: "0.01",
                      min: "0",
                      max: "1",
                      value: state.compForm.coefficient,
                      oninput: (e) => {
                        state.compForm.coefficient = e.target.value;
                      },
                    }),
                  ]),
                  m("label", [
                    "基准气温（℃）",
                    m("input", {
                      type: "number",
                      step: "0.1",
                      value: state.compForm.base_temperature,
                      oninput: (e) => {
                        state.compForm.base_temperature = e.target.value;
                      },
                    }),
                  ]),
                  m("button", { type: "submit" }, "保存系数"),
                ]),
                state.compError ? m("p.err", state.compError) : null,
                state.compMsg ? m("p.ok", state.compMsg) : null,
              ]
            )
          : null,
      ]),

      // 报送栏：只在温度补偿专页，现场气温必带。
      isWriter
        ? m("div.card", [
            m("h2", { style: { marginTop: 0, fontSize: "1.1rem" } }, "报送读数"),
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
                      body: JSON.stringify({
                        span_code: state.submitForm.span_code,
                        microstrain: parseFloat(state.submitForm.microstrain),
                        field_temperature: parseFloat(state.submitForm.field_temperature),
                      }),
                    });
                    state.msg = `${data.message}（原文 ${fmt(data.microstrain)} → 补偿后 ${fmt(
                      data.compensated_microstrain
                    )}，判定：${data.verdict}）`;
                    state.submitForm = { span_code: "", microstrain: "", field_temperature: "" };
                    await loadAll();
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
                    "微应变原文（με）",
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
                      value: state.submitForm.field_temperature,
                      oninput: (e) => {
                        state.submitForm.field_temperature = e.target.value;
                      },
                    }),
                  ]),
                  m("button", { type: "submit", disabled: state.loading }, "报送"),
                ]),
                state.error ? m("p.err", state.error) : null,
                state.msg ? m("p.ok", state.msg) : null,
              ]
            ),
          ])
        : null,

      // 账本区：原文与补偿后读数各一份快照，并与在线单对拍。
      m("div.card", [
        m("h2", { style: { marginTop: 0, fontSize: "1.1rem" } }, [
          "补偿账本（只追加）",
          m("span.hint", "（账本旧值不可改；与在线单对拍，单据被改即显示不一致）"),
        ]),
        m("div.tablewrap", [
          m("table", [
            m("thead", [
              m("tr", [
                m("th", "账本号"),
                m("th", "在线单号"),
                m("th", "跨段"),
                m("th", "原文读数"),
                m("th", "现场气温"),
                m("th", "系数"),
                m("th", "基准气温"),
                m("th", "补偿后读数"),
                m("th", "账本结论"),
                m("th", "记录人"),
                m("th", "对拍"),
              ]),
            ]),
            m(
              "tbody",
              state.ledger.length
                ? state.ledger.map((l) =>
                    m("tr", { key: l.id }, [
                      m("td", l.id),
                      m("td", l.reading_id),
                      m("td", l.span_code),
                      m("td", `${fmt(l.raw_microstrain)} με`),
                      m("td", `${fmt(l.field_temperature, 1)}℃`),
                      m("td", fmt(l.coefficient, 2)),
                      m("td", `${fmt(l.base_temperature, 1)}℃`),
                      m("td", `${fmt(l.compensated_microstrain)} με`),
                      m("td", m("span", { class: l.verdict === "合格" ? "tag pass" : "tag fail" }, l.verdict)),
                      m("td", l.recorded_by),
                      m("td", m(ReconcileTag, { l })),
                    ])
                  )
                : [m("tr", m("td", { colspan: 11 }, "账本暂无记录"))]
            ),
          ]),
        ]),
      ]),
    ];
  },
};

const ReconcileTag = {
  view: ({ attrs: { l } }) => {
    if (!l.online_exists) return m("span.tag.fail", "在线单缺失");
    if (l.values_match)
      return m("span.tag.pass", "一致");
    return m(
      "span.tag.fail",
      `不一致（在线：原文 ${fmt(l.online_raw)} / 补偿后 ${fmt(l.online_compensated)} / ${l.online_verdict || "无结论"}）`
    );
  },
};

const App = {
  oninit() {
    if (state.token) {
      loadAll();
      startPolling();
    }
  },
  onremove() {
    if (state.timer) clearInterval(state.timer);
  },
  view() {
    if (!state.token) return m(LoginView);
    const isWriter = state.user?.role === "writer";
    return m("div.wrap", [
      topbar(isWriter),
      state.page === "comp" ? m(CompensationPage) : m(ReadingListView),
    ]);
  },
};

export default App;
