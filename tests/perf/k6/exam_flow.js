// tests/perf/k6/exam_flow.js
// TASK-QA-003 · 性能冒烟：100 并发作答（保存 / 读取接口 P95 < 300ms）
//
// 运行（仓库根目录）：
//   python tests/perf/prepare_data.py
//   k6 run tests/perf/k6/exam_flow.js
//
// 选中该文件而不是 tests/perf/k6/login_setup.js 的理由：
//   - k6 的 setup() 本来就在独立 VU 中执行一次，登录集中在这里即可，不需要再拆模块；
//   - setup 的 Cookie jar 不会随返回值传给各 VU，所以无论脚本怎么拆，都必须把
//     session_id Cookie 值显式放进 setup 返回值、由 VU 用 `Cookie` 头回放（见 authHeaders）；
//   - 单文件让“登录 → 场景 → 阈值 → summary”保持在同一处，CI 只需 `k6 run` 一个路径，
//     少一层模块解析，降低路径/版本差异带来的故障面。
//
// 两个 scenario（k6 会自动给请求打 `scenario:<name>` 标签，阈值按标签取 P95）：
//   answer_save  100 VU：进入/恢复会话（仅首次）→ 随机作答 → 每 30s 保存一次
//                        → 末段按比例交卷（默认 20%，见 DEFAULT_SUBMIT_RATIO）。这是 100 名真实考生。
//   session_read 100 VU：复用同一批 100 名学生的会话做只读刷新（GET /sessions/{id}），
//                        默认 0.5s 间隔。它代表同一批考生的刷新/重连流量，不新增考生，
//                        目的是在同等并发量级上单独度量读取路径；总 VU 数因此是 200。
//
// 环境变量见 tests/perf/README.md；口令只从 EXAM_QA_PASSWORD 读取，不写进仓库。

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Trend } from 'k6/metrics';
import exec from 'k6/execution';

// --------------------------------------------------------------------------- //
// 常量与初始化期校验
// --------------------------------------------------------------------------- //
const DEFAULT_API_URL = 'http://127.0.0.1:8000/api';
const DEFAULT_DURATION = '3m';
const DEFAULT_VUS = 100;
const DEFAULT_READ_VUS = 100;
const DEFAULT_SAVE_INTERVAL = '30s';
const DEFAULT_READ_INTERVAL = '0.5s';
const DEFAULT_SAVE_ANSWERS = 10;
// 默认只让 20% 的会话在末段交卷：交卷要走“判分 + 错题本 + 审计”的写事务，
// 100 个 VU 同时交卷会形成评分尖峰并把评分耗时混进 answer_save 的 P95。
// 20 个交卷样本约占 3 分钟用例总样本的 3%–4%（< 5%），不会改变 P95 门线的含义；
// 需要完整压测交卷链路时显式设置 PERF_SUBMIT_RATIO=1。
const DEFAULT_SUBMIT_RATIO = 0.2;
const SESSION_COOKIE = 'session_id';
const CSRF_HEADER = 'X-CSRF-Token';
const DURATION_UNITS = { ms: 1, s: 1000, m: 60000, h: 3600000 };

function fail(message) {
  throw new Error('[TASK-QA-003] ' + message);
}

function parseDurationMs(raw) {
  const value = String(raw).trim();
  if (value === '0') {
    return 0;
  }
  const pattern = /(\d+(?:\.\d+)?)(ms|s|m|h)/g;
  let total = 0;
  let found = false;
  let match = pattern.exec(value);
  while (match !== null) {
    found = true;
    total += parseFloat(match[1]) * DURATION_UNITS[match[2]];
    match = pattern.exec(value);
  }
  if (!found) {
    fail('无法解析时长：' + value + '（示例：30s、1m30s、3m）');
  }
  return Math.round(total);
}

function envInt(name, fallback) {
  const raw = __ENV[name];
  if (raw === undefined || raw === null || String(raw).trim() === '') {
    return fallback;
  }
  const parsed = Number(raw);
  if (!isFinite(parsed) || parsed <= 0) {
    fail(name + ' 必须是正整数，当前为：' + raw);
  }
  return Math.floor(parsed);
}

function envFloat(name, fallback) {
  const raw = __ENV[name];
  if (raw === undefined || raw === null || String(raw).trim() === '') {
    return fallback;
  }
  const parsed = Number(raw);
  if (!isFinite(parsed)) {
    fail(name + ' 必须是数字，当前为：' + raw);
  }
  return parsed;
}

// 读取 prepare_data.py 产出的学生清单。
// k6 的 open() 只允许在 init context 调用，且相对路径以**脚本文件**为基准
// （`tests/perf/k6/../data/students.json` = `tests/perf/data/students.json`）。
// 第二个候选仅用于兼容历史上以仓库根为基准的 k6 打包方式。
function loadStudentData() {
  const inline = __ENV.EXAM_STUDENT_JSON;
  if (inline !== undefined && inline !== null && String(inline).trim() !== '') {
    try {
      return { source: 'EXAM_STUDENT_JSON', data: JSON.parse(inline) };
    } catch (err) {
      fail('EXAM_STUDENT_JSON 不是合法 JSON：' + err.message);
    }
  }

  const candidates = [];
  if (__ENV.EXAM_STUDENT_FILE) {
    candidates.push(__ENV.EXAM_STUDENT_FILE);
  }
  candidates.push('../data/students.json');
  candidates.push('tests/perf/data/students.json');

  for (let index = 0; index < candidates.length; index += 1) {
    const candidate = candidates[index];
    try {
      const raw = open(candidate, 'r');
      return { source: candidate, data: JSON.parse(raw) };
    } catch (err) {
      // 换下一个候选路径
    }
  }
  fail(
    '找不到学生数据文件。先运行 `python tests/perf/prepare_data.py`，' +
      '或设置 EXAM_STUDENT_FILE 指向 prepare_data.py 的输出。'
  );
  return null;
}

const LOADED = loadStudentData();
const DATA = LOADED.data;
if (!DATA || typeof DATA !== 'object') {
  fail('学生数据文件格式错误：顶层必须是 JSON 对象');
}
const STUDENTS = Array.isArray(DATA.students) ? DATA.students : [];
if (STUDENTS.length === 0) {
  fail('学生数据为空；请重新运行 prepare_data.py');
}
const EXAM_ID = DATA.exam && Number(DATA.exam.exam_id);
if (!EXAM_ID) {
  fail('学生数据缺少 exam.exam_id；请重新运行 prepare_data.py');
}

const RUN_ID = __ENV.EXAM_RUN_ID || DATA.run_id || 'perf-local';
if (__ENV.EXAM_RUN_ID && DATA.run_id && __ENV.EXAM_RUN_ID !== DATA.run_id) {
  fail(
    'EXAM_RUN_ID 与数据文件里的 run_id 不一致（' +
      __ENV.EXAM_RUN_ID +
      ' != ' +
      DATA.run_id +
      '）；避免把 A 环境的账号用 B 环境的考试。',
  );
}

const API_URL = (__ENV.EXAM_API_URL || DATA.api_url || DEFAULT_API_URL).replace(/\/+$/, '');
const QA_PASSWORD = __ENV.EXAM_QA_PASSWORD;
if (!QA_PASSWORD) {
  fail('EXAM_QA_PASSWORD 未设置；必须与 prepare_data.py 创建测试账号时使用的口令一致');
}

const VUS = envInt('PERF_VUS', DEFAULT_VUS);
const READ_VUS = envInt('PERF_READ_VUS', DEFAULT_READ_VUS);
const DURATION_STR = __ENV.PERF_DURATION || DEFAULT_DURATION;
const DURATION_MS = parseDurationMs(DURATION_STR);
const SAVE_INTERVAL_MS = parseDurationMs(
  __ENV.PERF_SAVE_INTERVAL || DEFAULT_SAVE_INTERVAL,
);
const READ_INTERVAL_MS = parseDurationMs(
  __ENV.PERF_READ_INTERVAL || DEFAULT_READ_INTERVAL,
);
const SAVE_ANSWERS = envInt('PERF_SAVE_ANSWERS', DEFAULT_SAVE_ANSWERS);
const SUBMIT_RATIO = envFloat('PERF_SUBMIT_RATIO', DEFAULT_SUBMIT_RATIO);

if (VUS > STUDENTS.length) {
  fail(
    'PERF_VUS=' +
      VUS +
      ' 超过学生数 ' +
      STUDENTS.length +
      '；每个写 VU 必须独占一个学生会话。请用 prepare_data.py 造更多学生或调低 PERF_VUS。',
  );
}
if (SUBMIT_RATIO < 0 || SUBMIT_RATIO > 1) {
  fail('PERF_SUBMIT_RATIO 必须在 [0, 1] 区间');
}
if (DURATION_MS < 10000) {
  fail('PERF_DURATION 至少 10s（否则交卷窗口与 sleep 无法形成有效样本）');
}
if (SAVE_INTERVAL_MS <= 0) {
  fail('PERF_SAVE_INTERVAL 必须大于 0（题目要求每 30s 真实 sleep 一次）');
}

// 末段按比例交卷；提前 20% 或最多 60s 进入交卷窗口，避免 gracefulStop 打断最后一次
// sleep 导致 VU 来不及交卷。
const SUBMIT_TAIL_MS = Math.min(60000, Math.max(5000, DURATION_MS * 0.2));
const SUBMIT_AT_MS = Math.max(0, DURATION_MS - SUBMIT_TAIL_MS);
const REPORT_DIR = (__ENV.PERF_REPORT_DIR || 'reports/perf/' + RUN_ID).replace(
  /\/+$/,
  '',
);

// --------------------------------------------------------------------------- //
// 自定义指标（Trend 名沿用任务卡要求的 save_p95 / read_p95 / submit_duration）
// --------------------------------------------------------------------------- //
const saveP95 = new Trend('save_p95', true);
const readP95 = new Trend('read_p95', true);
const submitDuration = new Trend('submit_duration', true);
const sessionStartDuration = new Trend('session_start_duration', true);
const loginDuration = new Trend('login_duration', true);

// --------------------------------------------------------------------------- //
// k6 选项：场景 + 阈值（不达标时 k6 以退出码 99 结束，CI 直接判红）
// --------------------------------------------------------------------------- //
export const options = {
  scenarios: {
    answer_save: {
      executor: 'constant-vus',
      exec: 'answerFlow',
      vus: VUS,
      duration: DURATION_STR,
      gracefulStop: '45s',
      tags: { flow: 'answer' },
    },
    session_read: {
      executor: 'constant-vus',
      exec: 'readFlow',
      vus: READ_VUS,
      duration: DURATION_STR,
      startTime: '5s',
      gracefulStop: '15s',
      tags: { flow: 'read' },
    },
  },
  thresholds: {
    // 任务卡门线：保存 / 读取接口在各自 100 VU 场景下 P95 < 300ms
    'http_req_duration{scenario:answer_save}': ['p(95)<300'],
    'http_req_duration{scenario:session_read}': ['p(95)<300'],
    // 全局错误率 < 0.1%（4xx/5xx 与网络错误都计入）
    http_req_failed: ['rate<0.001'],
    // 同口径的显式自定义指标，便于报告直接引用 save_p95 / read_p95
    save_p95: ['p(95)<300'],
    read_p95: ['p(95)<300'],
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
  setupTimeout: '5m',
  teardownTimeout: '30s',
  discardResponseBodies: false,
};

// --------------------------------------------------------------------------- //
// 工具：请求头 / 随机数 / 取学生
// --------------------------------------------------------------------------- //
function authHeaders(student, withJsonBody) {
  const headers = {
    Cookie: SESSION_COOKIE + '=' + student.cookie,
  };
  headers[CSRF_HEADER] = student.csrf;
  if (withJsonBody) {
    headers['Content-Type'] = 'application/json';
  }
  return headers;
}

function extractSessionCookie(response) {
  const cookies = response.cookies || {};
  const entries = cookies[SESSION_COOKIE];
  if (!entries || entries.length === 0) {
    return null;
  }
  return entries[0].value;
}

function studentForVu(setupData, vuId) {
  const count = setupData.students.length;
  const index = (((vuId - 1) % count) + count) % count;
  const record = setupData.students[index];
  return {
    username: record.username,
    cookie: record.cookie,
    csrf: record.csrf,
    index: index,
  };
}

function makeRng(seed) {
  let value = seed >>> 0;
  return function nextRandom() {
    value = (value + 0x6d2b79f5) >>> 0;
    let t = value;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function shuffle(items, rng) {
  const copy = items.slice();
  for (let index = copy.length - 1; index > 0; index -= 1) {
    const swapWith = Math.floor(rng() * (index + 1));
    const tmp = copy[index];
    copy[index] = copy[swapWith];
    copy[swapWith] = tmp;
  }
  return copy;
}

function randomAnswers(questions, rng) {
  if (!questions.length) {
    return [];
  }
  const target = Math.min(SAVE_ANSWERS, questions.length);
  const picked = [];
  const used = {};
  let guard = 0;
  const guardLimit = questions.length * 4 + 10;
  while (picked.length < target && guard < guardLimit) {
    guard += 1;
    const index = Math.floor(rng() * questions.length);
    if (used[index]) {
      continue;
    }
    used[index] = true;
    const question = questions[index];
    const options = question.options || [];
    const keys = [];
    for (let optionIndex = 0; optionIndex < options.length; optionIndex += 1) {
      keys.push(options[optionIndex].key);
    }
    if (!keys.length) {
      continue;
    }
    let selected;
    if (question.type === 'multiple') {
      const maxPick = Math.min(keys.length, 3);
      const pickCount = 1 + Math.floor(rng() * maxPick);
      selected = shuffle(keys, rng).slice(0, Math.max(1, pickCount));
    } else {
      selected = [keys[Math.floor(rng() * keys.length)]];
    }
    picked.push({ question_id: question.question_id, selected: selected });
  }
  return picked;
}

// --------------------------------------------------------------------------- //
// setup：100 名学生各登录一次，返回 Cookie + CSRF（只返回非敏感会话材料）
// --------------------------------------------------------------------------- //
function loginBatch(batch) {
  const requests = [];
  for (let index = 0; index < batch.length; index += 1) {
    requests.push({
      method: 'POST',
      url: API_URL + '/auth/login',
      body: JSON.stringify({
        username: batch[index].username,
        password: QA_PASSWORD,
      }),
      params: {
        headers: { 'Content-Type': 'application/json' },
        tags: { phase: 'setup_login' },
      },
    });
  }
  return http.batch(requests);
}

export function setup() {
  const sessions = [];
  const batchSize = 10;
  for (let offset = 0; offset < STUDENTS.length; offset += batchSize) {
    const batch = STUDENTS.slice(offset, offset + batchSize);
    const responses = loginBatch(batch);
    if (responses.length !== batch.length) {
      fail('setup 登录响应数量与请求数量不一致');
    }
    for (let index = 0; index < responses.length; index += 1) {
      const response = responses[index];
      const student = batch[index];
      if (response.status !== 200) {
        fail(
          '学生 ' +
            student.username +
            ' 登录失败：HTTP ' +
            response.status +
            '（检查 EXAM_QA_PASSWORD 是否与 prepare_data.py 创建账号时一致）',
        );
      }
      let payload;
      try {
        payload = response.json();
      } catch (err) {
        fail('学生 ' + student.username + ' 的登录响应不是合法 JSON');
      }
      if (!payload || !payload.csrf_token) {
        fail('学生 ' + student.username + ' 的登录响应缺少 csrf_token');
      }
      const cookie = extractSessionCookie(response);
      if (!cookie) {
        fail(
          '学生 ' +
            student.username +
            ' 的登录响应未下发 ' +
            SESSION_COOKIE +
            ' Cookie',
        );
      }
      sessions.push({
        username: student.username,
        display_name: student.display_name || student.username,
        cookie: cookie,
        csrf: String(payload.csrf_token),
      });
      loginDuration.add(response.timings.duration);
    }
  }

  console.log(
    '[setup] 已登录 ' +
      sessions.length +
      ' 名学生；exam_id=' +
      EXAM_ID +
      '；api=' +
      API_URL +
      '；数据文件=' +
      LOADED.source,
  );
  return {
    students: sessions,
    examId: EXAM_ID,
    baseUrl: API_URL,
    runId: RUN_ID,
  };
}

// --------------------------------------------------------------------------- //
// answer_save 场景：开始/恢复会话（仅首次）→ 随机作答 → 每 30s 保存 → 末段交卷
// --------------------------------------------------------------------------- //
const answerState = {
  ready: false,
  submitted: false,
  sessionId: 0,
  questions: [],
  clientSeq: 0,
  startedAt: 0,
  rng: null,
};

function startSession(student, setupData) {
  const response = http.post(
    setupData.baseUrl + '/exams/' + setupData.examId + '/sessions',
    null,
    {
      headers: authHeaders(student, false),
      tags: { phase: 'session_start' },
    },
  );
  sessionStartDuration.add(response.timings.duration);

  if (response.status === 404 || response.status === 409) {
    exec.test.abort(
      '无法开始会话（HTTP ' +
        response.status +
        '）：请先重新运行 `python tests/perf/prepare_data.py` 生成进行中的考试，' +
        '并确认 EXAM_API_URL 指向 prepare_data.py 使用的同一环境。',
    );
  }
  check(response, {
    'session start 200/201': function (res) {
      return res.status === 200 || res.status === 201;
    },
  });

  let session;
  try {
    session = response.json();
  } catch (err) {
    fail('开始会话响应不是合法 JSON（HTTP ' + response.status + '）');
  }
  if (!session || !session.id) {
    fail('开始会话响应缺少 session id');
  }
  if (session.status !== 'in_progress') {
    exec.test.abort(
      '学生会话已处于 ' +
        session.status +
        ' 状态：同一场考试不能重复压测，请重新运行 prepare_data.py（会自动轮换考试）。',
    );
  }
  if (!session.questions || session.questions.length === 0) {
    fail('SessionState.questions 为空，无法随机作答');
  }

  answerState.sessionId = session.id;
  answerState.questions = session.questions;
  answerState.startedAt = Date.now();
  answerState.rng = makeRng(exec.vu.idInTest * 2654435761);
  answerState.ready = true;
}

function saveCurrentAnswers(student, setupData) {
  const answers = randomAnswers(answerState.questions, answerState.rng);
  if (answers.length === 0) {
    return;
  }
  answerState.clientSeq += 1;
  const payload = JSON.stringify({
    client_seq: answerState.clientSeq,
    answers: answers,
  });
  const response = http.put(
    setupData.baseUrl + '/sessions/' + answerState.sessionId + '/answers',
    payload,
    {
      headers: authHeaders(student, true),
      tags: { phase: 'save' },
    },
  );
  saveP95.add(response.timings.duration);
  check(response, {
    'answer save 200': function (res) {
      return res.status === 200;
    },
  });
  if (response.status === 409) {
    // 到期/已交卷才会出现；标记后停止继续保存，避免持续 4xx 拉高错误率
    answerState.submitted = true;
  }
}

function isInSubmitQuota(student, setupData) {
  // 按比例交卷：索引落在配额内的学生交卷，其余学生保持 in_progress 到场景结束
  const quota = Math.round(SUBMIT_RATIO * setupData.students.length);
  return student.index < quota;
}

function submitSession(student, setupData) {
  answerState.submitted = true;
  const response = http.post(
    setupData.baseUrl + '/sessions/' + answerState.sessionId + '/submit',
    null,
    {
      headers: authHeaders(student, false),
      tags: { phase: 'submit' },
    },
  );
  submitDuration.add(response.timings.duration);
  const passed = check(response, {
    'submit 200': function (res) {
      return res.status === 200;
    },
  });
  if (!passed) {
    console.log(
      '[perf] submit 失败：student=' +
        student.username +
        ' HTTP=' +
        response.status,
    );
  }
}

export function answerFlow(setupData) {
  if (answerState.submitted) {
    sleep(1);
    return;
  }
  const student = studentForVu(setupData, exec.vu.idInTest);
  if (!answerState.ready) {
    startSession(student, setupData);
  }

  const elapsed = Date.now() - answerState.startedAt;
  if (elapsed >= SUBMIT_AT_MS && isInSubmitQuota(student, setupData)) {
    submitSession(student, setupData);
    return;
  }

  saveCurrentAnswers(student, setupData);
  // 题目要求“每 VU 每 30s 真实发送一次保存（用 sleep）”
  sleep(SAVE_INTERVAL_MS / 1000);
}

// --------------------------------------------------------------------------- //
// session_read 场景：复用同一批学生的会话，只读刷新
// --------------------------------------------------------------------------- //
const readState = { sessionId: 0 };

function ensureReadSession(student, setupData) {
  if (readState.sessionId) {
    return;
  }
  const response = http.post(
    setupData.baseUrl + '/exams/' + setupData.examId + '/sessions',
    null,
    {
      headers: authHeaders(student, false),
      tags: { phase: 'session_start' },
    },
  );
  sessionStartDuration.add(response.timings.duration);
  if (response.status === 404 || response.status === 409) {
    exec.test.abort(
      '读取场景无法开始会话（HTTP ' +
        response.status +
        '）：请先重新运行 `python tests/perf/prepare_data.py`。',
    );
  }
  check(response, {
    'read session start 200/201': function (res) {
      return res.status === 200 || res.status === 201;
    },
  });
  let session;
  try {
    session = response.json();
  } catch (err) {
    fail('读取场景开始会话响应不是合法 JSON');
  }
  if (!session || !session.id) {
    fail('读取场景开始会话响应缺少 session id');
  }
  readState.sessionId = session.id;
}

export function readFlow(setupData) {
  const student = studentForVu(setupData, exec.vu.idInTest);
  ensureReadSession(student, setupData);

  const response = http.get(
    setupData.baseUrl + '/sessions/' + readState.sessionId,
    {
      headers: authHeaders(student, false),
      tags: { phase: 'read' },
      // 读取场景只需要状态码与耗时：丢弃响应体，避免 100 题快照 × 200 rps 的 GC 压力
      // 反过来污染被测 API 的延迟（压测机与 API 同机时尤其明显）。
      responseType: 'none',
    },
  );
  readP95.add(response.timings.duration);
  check(response, {
    'session read 200': function (res) {
      return res.status === 200;
    },
  });

  if (READ_INTERVAL_MS > 0) {
    sleep(READ_INTERVAL_MS / 1000);
  }
}

// --------------------------------------------------------------------------- //
// summary：stdout 打印阈值 / 关键指标，并把原始 summary 落到 reports/perf/<run_id>/
// 注意：目录需由 prepare_data.py 预先创建（k6 handleSummary 不会自动建目录）。
// --------------------------------------------------------------------------- //
function formatMetricValues(values) {
  const keys = [
    'avg',
    'min',
    'med',
    'p(90)',
    'p(95)',
    'p(99)',
    'max',
    'rate',
    'count',
    'passes',
    'fails',
  ];
  const parts = [];
  for (let index = 0; index < keys.length; index += 1) {
    const key = keys[index];
    if (typeof values[key] === 'number') {
      parts.push(key + '=' + values[key].toFixed(2));
    }
  }
  return parts.join(' ');
}

export function handleSummary(data) {
  const metrics = data.metrics || {};
  const lines = [];
  lines.push('');
  lines.push('TASK-QA-003 性能冒烟摘要');
  lines.push(
    'run_id=' +
      RUN_ID +
      ' exam_id=' +
      EXAM_ID +
      ' api=' +
      API_URL +
      ' 学生数=' +
      STUDENTS.length,
  );
  lines.push(
    'answer_save VU=' +
      VUS +
      ' session_read VU=' +
      READ_VUS +
      ' duration=' +
      DURATION_STR +
      ' save_interval_ms=' +
      SAVE_INTERVAL_MS +
      ' submit_ratio=' +
      SUBMIT_RATIO,
  );
  lines.push('');
  lines.push('阈值：');
  const metricNames = Object.keys(metrics);
  for (let index = 0; index < metricNames.length; index += 1) {
    const metric = metrics[metricNames[index]];
    if (!metric || !metric.thresholds) {
      continue;
    }
    const expressions = Object.keys(metric.thresholds);
    for (let thresholdIndex = 0; thresholdIndex < expressions.length; thresholdIndex += 1) {
      const expression = expressions[thresholdIndex];
      const result = metric.thresholds[expression] || {};
      lines.push(
        '  ' +
          (result.ok ? 'PASS' : 'FAIL') +
          '  ' +
          metricNames[index] +
          ' ' +
          expression,
      );
    }
  }
  lines.push('');
  lines.push('关键指标：');
  const keyMetrics = [
    'http_req_duration',
    'http_req_duration{scenario:answer_save}',
    'http_req_duration{scenario:session_read}',
    'save_p95',
    'read_p95',
    'submit_duration',
    'session_start_duration',
    'login_duration',
    'http_req_failed',
    'http_reqs',
  ];
  for (let index = 0; index < keyMetrics.length; index += 1) {
    const name = keyMetrics[index];
    if (!metrics[name] || !metrics[name].values) {
      continue;
    }
    lines.push('  ' + name + ': ' + formatMetricValues(metrics[name].values));
  }
  lines.push('');
  lines.push('失败 check：');
  const checks = (data.root_group && data.root_group.checks) || [];
  let failedChecks = 0;
  for (let index = 0; index < checks.length; index += 1) {
    const item = checks[index];
    if (item && item.fails > 0) {
      failedChecks += 1;
      lines.push(
        '  FAIL ' +
          item.name +
          '：fails=' +
          item.fails +
          ' passes=' +
          item.passes,
      );
    }
  }
  if (failedChecks === 0) {
    lines.push('  （无）');
  }
  lines.push('');
  lines.push('原始 summary: ' + REPORT_DIR + '/summary.json');

  const outputs = {};
  outputs[REPORT_DIR + '/summary.json'] = JSON.stringify(data, null, 2);
  outputs.stdout = lines.join('\n') + '\n';
  return outputs;
}
