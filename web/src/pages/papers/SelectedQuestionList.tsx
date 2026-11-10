import { Button, Card, Empty, InputNumber, Radio, Space, Tag, Typography } from 'antd';

import { QUESTION_TYPE_COLORS, QUESTION_TYPE_LABELS } from '../questions/model';
import {
  MAX_QUESTION_POINTS,
  MIN_QUESTION_POINTS,
  PARTIAL_RULES,
  PARTIAL_RULE_HINTS,
  PARTIAL_RULE_LABELS,
  formatPoints,
  isMultipleType,
} from './model';
import type { PaperDraftErrors, PartialRule, SelectedPaperQuestion } from './model';

export interface SelectedQuestionListProps {
  items: readonly SelectedPaperQuestion[];
  readOnly: boolean;
  errors: PaperDraftErrors;
  totalPoints: number;
  onPointsChange: (questionId: number, points: number) => void;
  onPartialRuleChange: (questionId: number, rule: PartialRule) => void;
  onMove: (index: number, delta: number) => void;
  onRemove: (questionId: number) => void;
}

/**
 * 组卷右侧：已选题目列表。
 *
 * - `order_no` 由数组顺序派生（第 1 行 = order_no 1），上移/下移即调整顺序
 * - 每题可设分值（InputNumber，0.01–100、两位小数）
 * - 多选题可切换 partial_rule（全对得分 / 按比例给分）
 * - frozen（readOnly）时所有输入禁用，分值以文本展示
 *
 * data-testid：papers-selected-{id}、papers-order-{id}、papers-points-{id}、
 * papers-points-error-{id}、papers-partial-{id}、papers-up-{id}、papers-down-{id}、papers-remove-{id}
 */
export default function SelectedQuestionList({
  items,
  readOnly,
  errors,
  totalPoints,
  onPointsChange,
  onPartialRuleChange,
  onMove,
  onRemove,
}: SelectedQuestionListProps) {
  return (
    <Card
      title={
        <Space align="center">
          <Typography.Text strong>已选题目</Typography.Text>
          <Typography.Text type="secondary">{items.length} 题</Typography.Text>
        </Space>
      }
      extra={
        <Space size={4}>
          <Typography.Text type="secondary">总分</Typography.Text>
          <Typography.Text strong data-testid="papers-total-points">
            {formatPoints(totalPoints)} 分
          </Typography.Text>
        </Space>
      }
    >
      {errors.questions ? (
        <Typography.Paragraph type="danger" style={{ marginTop: 0 }} data-testid="papers-questions-error">
          {errors.questions}
        </Typography.Paragraph>
      ) : null}
      {errors.total ? (
        <Typography.Paragraph type="danger" style={{ marginTop: 0 }} data-testid="papers-total-error">
          {errors.total}
        </Typography.Paragraph>
      ) : null}

      {items.length === 0 ? (
        <Empty description="请在左侧题库勾选题目后加入" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ) : (
        <Space direction="vertical" size={0} style={{ width: '100%' }}>
          {items.map((item, index) => {
            const pointError = errors.pointsByQuestion[item.question_id];
            return (
              <div
                key={item.question_id}
                data-testid={`papers-selected-${item.question_id}`}
                style={{
                  display: 'flex',
                  gap: 8,
                  padding: '10px 0',
                  borderBottom: '1px solid #f0f0f0',
                  alignItems: 'flex-start',
                }}
              >
                <Typography.Text strong style={{ width: 28, lineHeight: '32px' }}>
                  <span data-testid={`papers-order-${item.question_id}`}>{index + 1}</span>
                </Typography.Text>

                <div style={{ flex: 1, minWidth: 0 }}>
                  <Typography.Text ellipsis={{ tooltip: item.stem }} style={{ maxWidth: 380 }}>
                    {item.stem}
                  </Typography.Text>
                  <Space size={6} wrap style={{ marginTop: 4 }}>
                    <Tag color={QUESTION_TYPE_COLORS[item.type]}>{QUESTION_TYPE_LABELS[item.type]}</Tag>
                    {isMultipleType(item.type) ? (
                      <Radio.Group
                        size="small"
                        value={item.partial_rule}
                        disabled={readOnly}
                        onChange={(event) => {
                          onPartialRuleChange(item.question_id, event.target.value as PartialRule);
                        }}
                        options={PARTIAL_RULES.map((rule) => ({
                          label: PARTIAL_RULE_LABELS[rule],
                          value: rule,
                        }))}
                        data-testid={`papers-partial-${item.question_id}`}
                      />
                    ) : null}
                    {isMultipleType(item.type) ? (
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        {PARTIAL_RULE_HINTS[item.partial_rule]}
                      </Typography.Text>
                    ) : null}
                  </Space>
                </div>

                <div style={{ width: 104 }}>
                  {readOnly ? (
                    <Typography.Text data-testid={`papers-points-${item.question_id}`}>
                      {formatPoints(item.points)} 分
                    </Typography.Text>
                  ) : (
                    <InputNumber<number>
                      value={item.points}
                      min={MIN_QUESTION_POINTS}
                      max={MAX_QUESTION_POINTS}
                      step={0.01}
                      precision={2}
                      status={pointError ? 'error' : undefined}
                      style={{ width: 96 }}
                      onChange={(value) => {
                        onPointsChange(item.question_id, typeof value === 'number' ? value : 0);
                      }}
                      data-testid={`papers-points-${item.question_id}`}
                    />
                  )}
                  {pointError ? (
                    <Typography.Text
                      type="danger"
                      style={{ display: 'block', fontSize: 12, lineHeight: 1.4 }}
                      data-testid={`papers-points-error-${item.question_id}`}
                    >
                      {pointError}
                    </Typography.Text>
                  ) : null}
                </div>

                <Space size={0}>
                  <Button
                    type="text"
                    size="small"
                    disabled={readOnly || index === 0}
                    onClick={() => {
                      onMove(index, -1);
                    }}
                    data-testid={`papers-up-${item.question_id}`}
                  >
                    上移
                  </Button>
                  <Button
                    type="text"
                    size="small"
                    disabled={readOnly || index === items.length - 1}
                    onClick={() => {
                      onMove(index, 1);
                    }}
                    data-testid={`papers-down-${item.question_id}`}
                  >
                    下移
                  </Button>
                  <Button
                    type="text"
                    size="small"
                    danger
                    disabled={readOnly}
                    onClick={() => {
                      onRemove(item.question_id);
                    }}
                    data-testid={`papers-remove-${item.question_id}`}
                  >
                    移除
                  </Button>
                </Space>
              </div>
            );
          })}
        </Space>
      )}
    </Card>
  );
}
