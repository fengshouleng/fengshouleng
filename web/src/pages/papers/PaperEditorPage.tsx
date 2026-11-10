import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Card, Input, Space, Spin, Tag, Typography, message } from 'antd';
import { useNavigate, useParams } from 'react-router-dom';

import { ApiError, queryKeys } from '../../api/client';
import type { Question } from '../questions/model';
import QuestionPicker from './QuestionPicker';
import SelectedQuestionList from './SelectedQuestionList';
import { createPaper, getPaper, updatePaper } from './api';
import { useQuestionMap } from './hooks';
import {
  PAPER_FROZEN_DETAIL,
  PAPER_FROZEN_NOTICE,
  PAPER_STATUS_COLORS,
  PAPER_STATUS_LABELS,
  buildPaperPayload,
  clearQuestionError,
  describePaperApiError,
  emptyDraftErrors,
  formatPoints,
  fromPaperQuestions,
  hasDraftErrors,
  parseValidationError,
  roundPoints,
  sumPoints,
  toSelectedQuestion,
  validatePaperDraft,
} from './model';
import type {
  PaperCreate,
  PaperDraftErrors,
  PartialRule,
  SelectedPaperQuestion,
} from './model';

/**
 * 组卷编辑页（TASK-FE-003）。
 *
 * - 左侧题库选题（复用 GET /questions 的 keyword/type/tag 筛选与分页，勾选加入）
 * - 右侧已选题目：每题设分、上移/下移（order_no 由顺序派生）、多选切换 partial_rule
 * - 实时总分（Σ points）；保存前前端校验单题 (0,100]、总分 ≤10000、至少 1 题
 * - 提交体只含 title + questions（`total_points` 由服务端计算，见 PaperCreate 注释）
 * - frozen：整页只读并提示「已用于已发布考试，不可修改」（AC-10）
 * - 后端 422 按字段回显（`parseValidationError`）；写成功失效 `['papers']`
 *
 * data-testid：page-paper-editor、papers-title、papers-save、papers-total-points、
 * papers-points-{question_id}、papers-frozen-alert、papers-form-error
 */
export default function PaperEditorPage() {
  const { paperId: paperIdParam } = useParams<{ paperId?: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const paperId = paperIdParam === undefined ? null : Number(paperIdParam);
  const isEdit = paperId !== null && Number.isInteger(paperId) && paperId > 0;

  const paperQuery = useQuery({
    queryKey: queryKeys.papers({ paper_id: paperId ?? 'new' }),
    queryFn: () => getPaper(paperId ?? 0),
    enabled: isEdit,
  });

  const remoteQuestions = paperQuery.data?.questions;
  const questionIds = useMemo(
    () => (remoteQuestions ?? []).map((item) => item.question_id),
    [remoteQuestions],
  );
  const { questions: questionById, loading: detailsLoading } = useQuestionMap(questionIds);

  const [title, setTitle] = useState('');
  const [items, setItems] = useState<SelectedPaperQuestion[]>([]);
  const [errors, setErrors] = useState<PaperDraftErrors>(emptyDraftErrors);
  const [formError, setFormError] = useState<string | null>(null);
  const [initialized, setInitialized] = useState(!isEdit);

  const frozen = paperQuery.data?.status === 'frozen';
  const readOnly = frozen;

  // 编辑：等试卷详情 + 题目详情就绪后一次性灌入草稿（只执行一次，避免覆盖用户输入）。
  useEffect(() => {
    if (initialized) return;
    if (!isEdit) {
      setInitialized(true);
      return;
    }
    const paper = paperQuery.data;
    if (!paper || detailsLoading) return;
    setTitle(paper.title);
    setItems(fromPaperQuestions(paper, questionById));
    setInitialized(true);
  }, [initialized, isEdit, paperQuery.data, detailsLoading, questionById]);

  const totalPoints = useMemo(() => sumPoints(items), [items]);
  const addedIds = useMemo(() => items.map((item) => item.question_id), [items]);

  const invalidatePapers = (): void => {
    void queryClient.invalidateQueries({ queryKey: ['papers'] });
  };

  const saveMutation = useMutation({
    mutationFn: (payload: PaperCreate) =>
      isEdit && paperId !== null ? updatePaper(paperId, payload) : createPaper(payload),
    onSuccess: () => {
      invalidatePapers();
      message.success('保存成功');
      navigate('/papers');
    },
    onError: (error) => {
      setFormError(describePaperApiError(error, '保存失败，请稍后重试'));
      if (error instanceof ApiError && error.status === 422) {
        // 后端错误体无结构化字段，按 message 里的 loc 前缀回显到对应输入框
        setErrors(parseValidationError(error.message, items));
      }
    },
  });

  const handleAdd = (questions: Question[]): void => {
    setItems((previous) => [...previous, ...questions.map((question) => toSelectedQuestion(question))]);
    setErrors((previous) => ({ ...previous, questions: undefined }));
  };

  const handlePointsChange = (questionId: number, points: number): void => {
    setItems((previous) =>
      previous.map((item) =>
        item.question_id === questionId ? { ...item, points: roundPoints(points) } : item,
      ),
    );
    setErrors((previous) => clearQuestionError(previous, questionId));
  };

  const handlePartialRuleChange = (questionId: number, rule: PartialRule): void => {
    setItems((previous) =>
      previous.map((item) =>
        item.question_id === questionId ? { ...item, partial_rule: rule } : item,
      ),
    );
  };

  const handleMove = (index: number, delta: number): void => {
    setItems((previous) => {
      const target = index + delta;
      if (target < 0 || target >= previous.length) return previous;
      const next = [...previous];
      const [moved] = next.splice(index, 1);
      if (!moved) return previous;
      next.splice(target, 0, moved);
      return next;
    });
  };

  const handleRemove = (questionId: number): void => {
    setItems((previous) => previous.filter((item) => item.question_id !== questionId));
    setErrors((previous) => clearQuestionError(previous, questionId));
  };

  const handleSave = (): void => {
    if (readOnly || saveMutation.isPending) return;
    const nextErrors = validatePaperDraft(title, items);
    setErrors(nextErrors);
    if (hasDraftErrors(nextErrors)) {
      setFormError('请先修正表单中的错误后再保存');
      return;
    }
    setFormError(null);
    saveMutation.mutate(buildPaperPayload(title, items));
  };

  if (isEdit && paperQuery.isLoading) {
    return (
      <div data-testid="page-paper-editor" style={{ padding: 48, textAlign: 'center' }}>
        <Spin />
      </div>
    );
  }

  if (isEdit && paperQuery.isError) {
    return (
      <div data-testid="page-paper-editor">
        <Alert
          type="error"
          showIcon
          message={`加载试卷失败：${describePaperApiError(paperQuery.error, '请稍后重试')}`}
          action={
            <Button size="small" onClick={() => navigate('/papers')}>
              返回列表
            </Button>
          }
        />
      </div>
    );
  }

  if (isEdit && !initialized) {
    return (
      <div data-testid="page-paper-editor" style={{ padding: 48, textAlign: 'center' }}>
        <Spin />
        <div style={{ marginTop: 12 }}>
          <Typography.Text type="secondary">正在加载试卷题目…</Typography.Text>
        </div>
      </div>
    );
  }

  return (
    <div data-testid="page-paper-editor">
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <Card
          title={
            <Space align="center">
              <Typography.Title level={4} style={{ margin: 0 }}>
                {isEdit ? '编辑试卷' : '新建试卷'}
              </Typography.Title>
              {paperQuery.data ? (
                <Tag
                  color={PAPER_STATUS_COLORS[paperQuery.data.status]}
                  data-testid="papers-editor-status"
                >
                  {PAPER_STATUS_LABELS[paperQuery.data.status]}
                </Tag>
              ) : null}
            </Space>
          }
          extra={
            <Space>
              <Button
                onClick={() => {
                  navigate('/papers');
                }}
                data-testid="papers-back"
              >
                返回列表
              </Button>
              <Button
                type="primary"
                loading={saveMutation.isPending}
                disabled={readOnly}
                onClick={handleSave}
                data-testid="papers-save"
              >
                保存
              </Button>
            </Space>
          }
        >
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            {frozen ? (
              <div data-testid="papers-frozen-alert">
                <Alert type="warning" showIcon message={PAPER_FROZEN_NOTICE} description={PAPER_FROZEN_DETAIL} />
              </div>
            ) : null}

            {formError ? (
              <div data-testid="papers-form-error">
                <Alert type="error" showIcon message={formError} />
              </div>
            ) : null}

            <div>
              <Typography.Text strong>试卷标题</Typography.Text>
              <Input
                value={title}
                maxLength={128}
                showCount
                placeholder="例如：期中数学测验（10 题 × 10 分）"
                status={errors.title ? 'error' : undefined}
                disabled={readOnly}
                onChange={(event) => {
                  setTitle(event.target.value);
                  if (errors.title) setErrors((previous) => ({ ...previous, title: undefined }));
                }}
                data-testid="papers-title"
              />
              {errors.title ? (
                <Typography.Text type="danger" style={{ fontSize: 12 }} data-testid="papers-title-error">
                  {errors.title}
                </Typography.Text>
              ) : null}
            </div>

            <Space size={16} wrap align="center">
              <Typography.Text type="secondary">
                总分 {formatPoints(totalPoints)} 分 / 上限 10000 分
              </Typography.Text>
              <Typography.Text type="secondary">共 {items.length} 题（上限 100 题）</Typography.Text>
            </Space>
          </Space>
        </Card>

        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr)',
            gap: 16,
            alignItems: 'start',
          }}
        >
          <QuestionPicker addedIds={addedIds} readOnly={readOnly} onAdd={handleAdd} />
          <SelectedQuestionList
            items={items}
            readOnly={readOnly}
            errors={errors}
            totalPoints={totalPoints}
            onPointsChange={handlePointsChange}
            onPartialRuleChange={handlePartialRuleChange}
            onMove={handleMove}
            onRemove={handleRemove}
          />
        </div>
      </Space>
    </div>
  );
}
