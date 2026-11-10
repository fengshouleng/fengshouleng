import { useEffect } from 'react';
import { Alert, Button, Checkbox, Form, Input, Modal, Radio, Rate, Space, Typography } from 'antd';

import {
  JUDGE_OPTION_TEXTS,
  MAX_DIFFICULTY,
  MAX_OPTION_COUNT,
  MAX_TAG_LENGTH,
  MIN_DIFFICULTY,
  MIN_MULTIPLE_ANSWER_COUNT,
  MIN_OPTION_COUNT,
  QUESTION_TYPES,
  QUESTION_TYPE_LABELS,
  emptyFormValues,
  judgeFormOptions,
  optionKeyAt,
  parseTags,
  toFormValues,
} from './model';
import type { OptionFormValue, Question, QuestionFormValues, QuestionType } from './model';

const DIFFICULTY_TOOLTIPS = ['很简单', '较简单', '中等', '较难', '很难'];

export interface QuestionFormModalProps {
  open: boolean;
  mode: 'create' | 'edit';
  /** 编辑时传入当前题目；新建为 null。 */
  question: Question | null;
  submitting: boolean;
  /** 后端错误（422 / 403 / 网络等）回显文案，由页面统一映射。 */
  errorText: string | null;
  onCancel: () => void;
  onSubmit: (values: QuestionFormValues) => void;
}

/**
 * 新建 / 编辑题目弹窗。
 *
 * 题型约束（与契约 QuestionCreate 对齐）：
 * - single：选项 2–6 个，答案恰好 1 个
 * - multiple：选项 2–6 个，答案 ≥2 个（子集校验由表单选项保证）
 * - judge：选项固定 A=正确 / B=错误，不可增删，答案恰好 1 个
 * - explanation 必填（契约 C-10），difficulty 1–5，tags 逗号分隔
 */
export default function QuestionFormModal({
  open,
  mode,
  question,
  submitting,
  errorText,
  onCancel,
  onSubmit,
}: QuestionFormModalProps) {
  const [form] = Form.useForm<QuestionFormValues>();

  // 监听题型 / 选项 / 答案，驱动选项与答案控件联动。
  const type = (Form.useWatch('type', form) ?? 'single') as QuestionType;
  const options = (Form.useWatch('options', form) ?? []) as OptionFormValue[];
  const answers = (Form.useWatch('answer', form) ?? []) as number[];

  useEffect(() => {
    if (!open) return;
    form.resetFields();
    form.setFieldsValue(mode === 'edit' && question ? toFormValues(question) : emptyFormValues());
  }, [open, mode, question, form]);

  const answerChoices = options.map((option, index) => {
    const text = (option?.text ?? '').trim();
    return { label: `${optionKeyAt(index)}. ${text || '（未填写）'}`, value: index };
  });

  const validateAnswer = (_rule: unknown, value: unknown): Promise<void> => {
    const selected = Array.isArray(value) ? (value as number[]) : [];
    const currentType = (form.getFieldValue('type') as QuestionType | undefined) ?? 'single';
    if (currentType === 'multiple') {
      if (selected.length < MIN_MULTIPLE_ANSWER_COUNT) {
        return Promise.reject(
          new Error(`多选题请至少选择 ${MIN_MULTIPLE_ANSWER_COUNT} 个正确答案`),
        );
      }
      return Promise.resolve();
    }
    if (selected.length !== 1) {
      return Promise.reject(new Error('请选择 1 个正确答案'));
    }
    return Promise.resolve();
  };

  const validateTags = (_rule: unknown, value: unknown): Promise<void> => {
    const tooLong = parseTags(typeof value === 'string' ? value : '').find(
      (tag) => tag.length > MAX_TAG_LENGTH,
    );
    if (tooLong) {
      return Promise.reject(
        new Error(`标签「${tooLong}」不能超过 ${MAX_TAG_LENGTH} 个字符`),
      );
    }
    return Promise.resolve();
  };

  /**
   * 单选/判断题的 Radio.Group 只接受标量值，而表单模型统一用 number[]（多选需要数组）。
   * 通过 getValueProps / normalize 在两者之间转换，提交时永远是契约要求的数组。
   */
  const radioAnswerValueProps = (
    value: number[] | undefined,
  ): Record<string, number | undefined> => ({
    value: Array.isArray(value) ? value[0] : undefined,
  });

  const radioAnswerNormalize = (value: number | undefined): number[] =>
    typeof value === 'number' ? [value] : [];

  /** 题型切换：判断题锁定 A/B；从判断题切回可编辑题型时重置为 4 个空选项。 */
  const handleTypeChange = (nextType: QuestionType): void => {
    if (nextType === 'judge') {
      form.setFieldsValue({ options: judgeFormOptions(), answer: [] });
      return;
    }
    const currentOptions = (form.getFieldValue('options') ?? []) as OptionFormValue[];
    const looksLikeJudge =
      currentOptions.length === JUDGE_OPTION_TEXTS.length &&
      currentOptions.every((option, index) => option?.text === JUDGE_OPTION_TEXTS[index]);
    if (looksLikeJudge) {
      form.setFieldsValue({ options: emptyFormValues().options, answer: [] });
      return;
    }
    if (nextType === 'single') {
      const [first] = answers;
      form.setFieldValue('answer', first === undefined ? [] : [first]);
    }
  };

  /** 删除选项：答案下标同步平移 / 移除，避免答案指向已删除的选项。 */
  const handleRemoveOption = (index: number, remove: (index: number) => void): void => {
    if (options.length <= MIN_OPTION_COUNT) return;
    remove(index);
    const nextAnswers = answers
      .filter((item) => item !== index)
      .map((item) => (item > index ? item - 1 : item));
    form.setFieldValue('answer', nextAnswers);
  };

  return (
    <Modal
      open={open}
      title={mode === 'edit' ? '编辑题目' : '新建题目'}
      width={760}
      onCancel={onCancel}
      maskClosable={!submitting}
      keyboard={!submitting}
      footer={
        <Space>
          <Button onClick={onCancel} disabled={submitting}>
            取消
          </Button>
          <Button
            type="primary"
            loading={submitting}
            onClick={() => {
              if (!submitting) form.submit();
            }}
            data-testid="questions-submit"
          >
            保存
          </Button>
        </Space>
      }
      styles={{ body: { maxHeight: '62vh', overflowY: 'auto', paddingTop: 8 } }}
    >
      {errorText ? (
        <div data-testid="questions-form-error" style={{ marginBottom: 16 }}>
          <Alert type="error" showIcon message={errorText} />
        </div>
      ) : null}

      <Form<QuestionFormValues>
        form={form}
        layout="vertical"
        requiredMark
        onFinish={(values) => {
          onSubmit(values);
        }}
      >
        <Form.Item label="题型" name="type" rules={[{ required: true, message: '请选择题型' }]}>
          <Radio.Group
            onChange={(event) => {
              handleTypeChange(event.target.value as QuestionType);
            }}
          >
            {QUESTION_TYPES.map((item) => (
              <Radio.Button key={item} value={item}>
                {QUESTION_TYPE_LABELS[item]}
              </Radio.Button>
            ))}
          </Radio.Group>
        </Form.Item>

        <Form.Item
          label="题干"
          name="stem"
          rules={[{ required: true, whitespace: true, message: '请输入题干' }]}
        >
          <Input.TextArea
            rows={3}
            maxLength={2000}
            showCount
            placeholder="请输入题干"
            data-testid="questions-stem"
          />
        </Form.Item>

        <Form.Item label="选项" required={false} style={{ marginBottom: 8 }}>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {type === 'judge'
              ? `判断题固定为 A. ${JUDGE_OPTION_TEXTS[0]} / B. ${JUDGE_OPTION_TEXTS[1]}，不可增删。`
              : `选项按 A–F 自动编号，至少 ${MIN_OPTION_COUNT} 个、最多 ${MAX_OPTION_COUNT} 个。`}
          </Typography.Text>
        </Form.Item>

        <Form.List name="options">
          {(fields, { add, remove }) => (
            <>
              {fields.map((field, index) => (
                <div
                  key={field.key}
                  style={{ display: 'flex', gap: 8, alignItems: 'flex-start', marginBottom: 8 }}
                >
                  <Typography.Text strong style={{ width: 20, lineHeight: '32px' }}>
                    {optionKeyAt(index)}
                  </Typography.Text>
                  <Form.Item
                    {...field}
                    name={[field.name, 'text']}
                    rules={[{ required: true, whitespace: true, message: '请输入选项内容' }]}
                    style={{ marginBottom: 0, flex: 1 }}
                  >
                    <Input
                      placeholder={`选项 ${optionKeyAt(index)} 内容`}
                      maxLength={500}
                      disabled={type === 'judge'}
                      data-testid={`questions-option-text-${index}`}
                    />
                  </Form.Item>
                  {type === 'judge' ? null : (
                    <Button
                      type="link"
                      danger
                      size="small"
                      disabled={options.length <= MIN_OPTION_COUNT}
                      onClick={() => {
                        handleRemoveOption(index, remove);
                      }}
                      data-testid={`questions-option-remove-${index}`}
                    >
                      删除
                    </Button>
                  )}
                </div>
              ))}
              {type === 'judge' ? null : (
                <Button
                  type="dashed"
                  block
                  disabled={options.length >= MAX_OPTION_COUNT}
                  onClick={() => {
                    add({ text: '' });
                  }}
                  data-testid="questions-option-add"
                >
                  添加选项（最多 {MAX_OPTION_COUNT} 个）
                </Button>
              )}
            </>
          )}
        </Form.List>

        {/* 契约 must 中的 questions-create-answer-text：答案编辑区（单选/多选/判断共用）。 */}
        <div data-testid="questions-create-answer-text">
          <Form.Item
            label="正确答案"
            name="answer"
            rules={[{ validator: validateAnswer }]}
            style={{ marginTop: 16 }}
            getValueProps={type === 'multiple' ? undefined : radioAnswerValueProps}
            normalize={type === 'multiple' ? undefined : radioAnswerNormalize}
          >
            {type === 'multiple' ? (
              <Checkbox.Group options={answerChoices} />
            ) : (
              <Radio.Group options={answerChoices} />
            )}
          </Form.Item>
        </div>

        <Form.Item
          label="解析"
          name="explanation"
          rules={[{ required: true, whitespace: true, message: '请输入解析' }]}
          extra="解析必填：契约要求不允许保存没有解析的题目（C-10）。"
        >
          <Input.TextArea
            rows={3}
            maxLength={1000}
            showCount
            placeholder="请输入解析"
            data-testid="questions-explanation"
          />
        </Form.Item>

        <Form.Item
          label="难度"
          name="difficulty"
          rules={[
            { required: true, message: '请选择难度' },
            { type: 'number', min: MIN_DIFFICULTY, max: MAX_DIFFICULTY, message: '难度为 1–5' },
          ]}
        >
          <Rate allowClear={false} count={MAX_DIFFICULTY} tooltips={DIFFICULTY_TOOLTIPS} />
        </Form.Item>

        <Form.Item
          label="标签"
          name="tags"
          rules={[{ validator: validateTags }]}
          extra="多个标签用逗号分隔，例如：代数,期中"
        >
          <Input placeholder="代数,期中" maxLength={200} data-testid="questions-tags" />
        </Form.Item>
      </Form>
    </Modal>
  );
}
