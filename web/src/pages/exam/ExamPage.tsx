/**
 * 考试作答页（TASK-FE-004，关键路径）。
 *
 * - 进入即 POST /exams/{id}/sessions（201 新建 / 200 恢复），用 SessionState 渲染题目与答题卡；
 * - 倒计时基于 deadline_at + server_time 校时，≤5 分钟高亮，归零自动交卷；
 * - 自动保存 debounce 30s、切题/交卷前强制保存，client_seq 由 hook 统一递增；
 * - 交卷二次确认；提交后跳 /results/{session_id}（TASK-FE-005 页面，当前为占位页）；
 * - 409 EXAM_EXPIRED → 刷新会话状态并跳成绩页（不解析错误体）。
 */
import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Card, Col, Modal, Row, Space, Spin, Tag, Typography, message } from 'antd';
import { useNavigate, useParams } from 'react-router-dom';

import { useExamSession } from '../../hooks/useExamSession';
import {
  COUNTDOWN_URGENT_MS,
  SESSION_STATUS_COLORS,
  SESSION_STATUS_LABELS,
  formatDateTime,
  isSessionFinished,
} from './model';
import type { SessionStatus } from './model';

export interface ExamResultNavigationState {
  score: number | null;
  status: SessionStatus | 'expired';
  replayed?: boolean;
  notice?: string;
}

function parseExamId(raw: string | undefined): number | null {
  if (!raw || !/^\d+$/.test(raw)) return null;
  const value = Number(raw);
  return Number.isSafeInteger(value) && value > 0 ? value : null;
}

export default function ExamPage() {
  const params = useParams<{ examId: string }>();
  const examId = parseExamId(params.examId);
  const navigate = useNavigate();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const finishedRedirectRef = useRef(false);

  const {
    session,
    loading,
    loadError,
    retryLoad,
    answers,
    answeredCount,
    currentIndex,
    currentQuestion,
    goToQuestion,
    toggleOption,
    saveState,
    saveStatusText,
    remainingText,
    urgent,
    submitting,
    submitError,
    expireNotice,
    autoSubmitted,
    result,
    submit,
  } = useExamSession(examId);

  const resultNavigatedRef = useRef(false);

  // 交卷成功 → 成绩页（replayed=true 时提示“显示原成绩”）。
  useEffect(() => {
    if (!result || resultNavigatedRef.current) return;
    resultNavigatedRef.current = true;
    if (result.replayed) {
      // 契约 C-15：replayed=true 表示重复提交，成绩与首次一致
      message.info('已交卷，显示原成绩');
    } else if (autoSubmitted) {
      message.warning('考试时间已到，系统已自动交卷并判分');
    }
    const state: ExamResultNavigationState = {
      score: result.score,
      status: result.status,
      replayed: result.replayed,
      notice: autoSubmitted
        ? '考试时间已到，系统已自动交卷并判分'
        : result.replayed
          ? '已交卷，显示原成绩'
          : result.status === 'expired'
            ? '考试时间已到，系统已自动交卷并判分'
            : '交卷成功，成绩已生成',
    };
    navigate(`/results/${result.session_id}`, { replace: true, state });
  }, [result, autoSubmitted, navigate]);

  // 恢复到一个已交卷/已到期的会话（含 409 后刷新）→ 直接跳成绩页。
  useEffect(() => {
    if (!session || !isSessionFinished(session)) return;
    if (result) return;
    if (finishedRedirectRef.current) return;
    finishedRedirectRef.current = true;
    const notice =
      expireNotice ??
      (session.status === 'expired'
        ? '考试已到期，系统已自动交卷，成绩已生成'
        : '本场考试已交卷，显示原成绩');
    message.warning(notice);
    const state: ExamResultNavigationState = {
      score: session.score ?? null,
      status: session.status,
      notice,
    };
    navigate(`/results/${session.id}`, { replace: true, state });
  }, [session, result, expireNotice, navigate]);

  if (examId === null) {
    return (
      <div data-testid="exam-page">
        <Alert
          type="error"
          showIcon
          message="考试地址无效"
          description="请从「我的考试」列表进入考试。"
          action={
            <Button size="small" onClick={() => navigate('/my-exams')}>
              返回我的考试
            </Button>
          }
        />
      </div>
    );
  }

  if (loading) {
    return (
      <div data-testid="exam-page">
        <div data-testid="exam-loading" style={{ padding: 48, textAlign: 'center' }}>
          <Spin size="large" />
          <div style={{ marginTop: 12 }}>
            <Typography.Text type="secondary">正在进入考试…</Typography.Text>
          </div>
        </div>
      </div>
    );
  }

  if (loadError) {
    return (
      <div data-testid="exam-page">
        <Alert
          type="error"
          showIcon
          message="进入考试失败"
          description={loadError}
          action={
            <Space>
              <Button size="small" onClick={retryLoad} data-testid="exam-retry-load">
                重试
              </Button>
              <Button size="small" onClick={() => navigate('/my-exams')}>
                返回我的考试
              </Button>
            </Space>
          }
        />
      </div>
    );
  }

  if (!session) {
    return (
      <div data-testid="exam-page">
        <div data-testid="exam-loading" style={{ padding: 48, textAlign: 'center' }}>
          <Spin size="large" />
          <div style={{ marginTop: 12 }}>
            <Typography.Text type="secondary">正在载入题目…</Typography.Text>
          </div>
        </div>
      </div>
    );
  }

  if (!currentQuestion) {
    return (
      <div data-testid="exam-page">
        <Alert
          type="warning"
          showIcon
          message="本场考试暂无题目"
          description="试卷快照为空，请联系监考老师。"
          action={
            <Button size="small" onClick={() => navigate('/my-exams')}>
              返回我的考试
            </Button>
          }
        />
      </div>
    );
  }

  const total = session.questions.length;
  const saveColor =
    saveState === 'error' || saveState === 'offline'
      ? 'red'
      : saveState === 'stale'
        ? 'orange'
        : saveState === 'saved'
          ? 'green'
          : 'default';

  return (
    <div data-testid="exam-page">
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <Card>
          <Row gutter={16} align="middle" justify="space-between">
            <Col>
              <Space size="large" wrap>
                <Typography.Title level={4} style={{ margin: 0 }}>
                  在线考试
                </Typography.Title>
                <Tag color={SESSION_STATUS_COLORS[session.status]}>
                  {SESSION_STATUS_LABELS[session.status]}
                </Tag>
                <Typography.Text type="secondary">
                  会话 #{session.id} · 截止 {formatDateTime(session.deadline_at)}
                </Typography.Text>
              </Space>
            </Col>
            <Col>
              <Space size="middle" wrap>
                <div
                  data-testid="exam-countdown"
                  data-urgent={urgent ? 'true' : 'false'}
                  style={{
                    fontVariantNumeric: 'tabular-nums',
                    fontSize: 22,
                    fontWeight: 700,
                    color: urgent ? '#cf1322' : '#1677ff',
                  }}
                  title={`剩余时间（≤${COUNTDOWN_URGENT_MS / 60000} 分钟高亮）`}
                >
                  {remainingText}
                </div>
                <span data-testid="exam-save-status">
                  <Tag color={saveColor}>{saveStatusText}</Tag>
                </span>
                <Button
                  type="primary"
                  danger
                  disabled={submitting || session.status !== 'in_progress'}
                  loading={submitting}
                  onClick={() => setConfirmOpen(true)}
                  data-testid="exam-submit"
                >
                  交卷
                </Button>
              </Space>
            </Col>
          </Row>

          {expireNotice ? (
            <div data-testid="exam-expired-notice" style={{ marginTop: 12 }}>
              <Alert type="warning" showIcon message={expireNotice} />
            </div>
          ) : null}
        </Card>

        <Row gutter={16}>
          <Col xs={24} lg={17}>
            <Space direction="vertical" size={16} style={{ width: '100%' }}>
              <Card size="small">
                <Space wrap align="center">
                  <Typography.Text strong>
                    第 {currentIndex + 1} 题 / 共 {total} 题
                  </Typography.Text>
                  <Typography.Text type="secondary">
                    已答 {answeredCount} / {total} 题
                  </Typography.Text>
                  <Button
                    disabled={currentIndex <= 0}
                    onClick={() => goToQuestion(currentIndex - 1)}
                    data-testid="exam-prev"
                  >
                    上一题
                  </Button>
                  <Button
                    type="primary"
                    disabled={currentIndex >= total - 1}
                    onClick={() => goToQuestion(currentIndex + 1)}
                    data-testid="exam-next"
                  >
                    下一题
                  </Button>
                </Space>
              </Card>

              {session.questions.map((question, index) => {
                const selected = answers[question.question_id] ?? [];
                const multiple = question.type === 'multiple';
                const active = index === currentIndex;
                return (
                  <Card
                    key={question.question_id}
                    style={{
                      borderColor: active ? '#1677ff' : undefined,
                      boxShadow: active ? '0 0 0 1px rgba(22, 119, 255, 0.2)' : undefined,
                    }}
                    title={
                      <Space wrap>
                        <span>第 {index + 1} 题</span>
                        <Tag color={multiple ? 'purple' : 'blue'}>
                          {question.type === 'single'
                            ? '单选题'
                            : multiple
                              ? '多选题'
                              : '判断题'}
                        </Tag>
                        <Typography.Text type="secondary">{question.points} 分</Typography.Text>
                      </Space>
                    }
                  >
                    <div data-testid={`exam-question-${question.question_id}`}>
                      <Typography.Paragraph style={{ fontSize: 16, whiteSpace: 'pre-wrap' }}>
                        {question.stem}
                      </Typography.Paragraph>

                      <Space direction="vertical" size={10} style={{ width: '100%' }}>
                        {question.options.map((option) => {
                          const checked = selected.includes(option.key);
                          return (
                            <label
                              key={option.key}
                              style={{
                                display: 'flex',
                                alignItems: 'flex-start',
                                gap: 10,
                                padding: '10px 12px',
                                border: `1px solid ${checked ? '#1677ff' : '#d9d9d9'}`,
                                borderRadius: 6,
                                background: checked ? '#e6f4ff' : '#fff',
                                cursor: 'pointer',
                              }}
                            >
                              <input
                                type={multiple ? 'checkbox' : 'radio'}
                                name={`question-${question.question_id}`}
                                value={option.key}
                                checked={checked}
                                onChange={() => toggleOption(question, option.key)}
                                data-testid={`answer-${question.question_id}-${option.key}`}
                                style={{ marginTop: 3 }}
                              />
                              <span style={{ lineHeight: '22px' }}>
                                <Typography.Text strong>{option.key}.</Typography.Text>{' '}
                                {option.text}
                              </span>
                            </label>
                          );
                        })}
                      </Space>
                    </div>
                  </Card>
                );
              })}
            </Space>
          </Col>

          <Col xs={24} lg={7}>
            <Card title="答题卡">
              <Space size={[8, 8]} wrap>
                {session.questions.map((question, index) => {
                  const answered = (answers[question.question_id] ?? []).length > 0;
                  return (
                    <Button
                      key={question.question_id}
                      size="small"
                      type={index === currentIndex ? 'primary' : answered ? 'default' : 'dashed'}
                      style={{
                        width: 40,
                        borderColor: answered && index !== currentIndex ? '#52c41a' : undefined,
                        color: answered && index !== currentIndex ? '#389e0d' : undefined,
                      }}
                      onClick={() => goToQuestion(index)}
                      data-testid={`exam-card-${question.question_id}`}
                      aria-label={`第 ${index + 1} 题${answered ? '（已答）' : ''}`}
                    >
                      {index + 1}
                    </Button>
                  );
                })}
              </Space>
              <Typography.Paragraph type="secondary" style={{ marginTop: 16, marginBottom: 0 }}>
                绿色边框表示已作答。切换题目会自动保存答案。
              </Typography.Paragraph>
            </Card>
          </Col>
        </Row>
      </Space>

      <Modal
        open={confirmOpen}
        title="确认交卷"
        maskClosable={!submitting}
        closable={!submitting}
        onCancel={() => {
          if (!submitting) setConfirmOpen(false);
        }}
        footer={
          <Space>
            <Button
              disabled={submitting}
              onClick={() => setConfirmOpen(false)}
              data-testid="exam-cancel-submit"
            >
              继续作答
            </Button>
            <Button
              type="primary"
              danger
              loading={submitting}
              disabled={submitting}
              onClick={() => {
                void submit();
              }}
              data-testid="exam-confirm-submit"
            >
              确认交卷
            </Button>
          </Space>
        }
      >
        <Typography.Paragraph>
          你已作答 {answeredCount} / {total} 题。交卷后不可再修改答案，系统将立即判分。
        </Typography.Paragraph>
        {submitError ? (
          <div data-testid="exam-submit-error">
            <Alert type="error" showIcon message={submitError} />
          </div>
        ) : null}
      </Modal>
    </div>
  );
}
