import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Alert, Button, Card, Input, Radio, Select, Space, Table, Tag, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';

import { queryKeys } from '../../api/client';
import { listQuestions } from '../questions/api';
import {
  QUESTION_TYPE_COLORS,
  QUESTION_TYPE_LABELS,
  QUESTION_TYPES,
  describeQuestionApiError,
} from '../questions/model';
import type { Question, QuestionType } from '../questions/model';

const PAGE_SIZE_OPTIONS = [10, 20, 50, 100];

export interface QuestionPickerProps {
  /** 已在试卷中的题目 id（禁止重复加入）。 */
  addedIds: readonly number[];
  /** frozen 试卷只读：筛选仍可用，但加入按钮禁用。 */
  readOnly: boolean;
  onAdd: (questions: Question[]) => void;
}

/**
 * 组卷左侧：题库选题区。
 *
 * 复用 `GET /questions` 的 keyword / type / tag 筛选与分页（与题库页同参数口径），
 * 通过表格勾选（rowSelection）多选后「加入已选」，也可单行「加入」。
 *
 * data-testid：papers-picker、papers-keyword、papers-tag-filter、papers-search、papers-add-selected、
 * papers-add-{id}
 */
export default function QuestionPicker({ addedIds, readOnly, onAdd }: QuestionPickerProps) {
  const [keywordDraft, setKeywordDraft] = useState('');
  const [tagDraft, setTagDraft] = useState('');
  const [keyword, setKeyword] = useState('');
  const [tag, setTag] = useState('');
  const [type, setType] = useState<QuestionType | undefined>(undefined);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [pickedIds, setPickedIds] = useState<number[]>([]);

  const params = useMemo(
    () => ({
      keyword: keyword || undefined,
      type,
      tag: tag || undefined,
      page,
      page_size: pageSize,
    }),
    [keyword, type, tag, page, pageSize],
  );

  const questionsQuery = useQuery({
    queryKey: queryKeys.questions({
      keyword: params.keyword,
      type: params.type,
      tag: params.tag,
      page: params.page,
      page_size: params.page_size,
    }),
    queryFn: () => listQuestions(params),
  });

  const items = questionsQuery.data?.items ?? [];
  const total = questionsQuery.data?.total ?? 0;
  const listError = questionsQuery.isError
    ? describeQuestionApiError(questionsQuery.error, '请稍后重试')
    : null;

  const addedSet = useMemo(() => new Set(addedIds), [addedIds]);
  const pickedQuestions = items.filter((question) => pickedIds.includes(question.id));

  const applySearch = (): void => {
    setKeyword(keywordDraft.trim());
    setTag(tagDraft.trim());
    setPage(1);
  };

  const resetFilters = (): void => {
    setKeywordDraft('');
    setTagDraft('');
    setKeyword('');
    setTag('');
    setType(undefined);
    setPage(1);
  };

  const addQuestions = (questions: Question[]): void => {
    const fresh = questions.filter((question) => !addedSet.has(question.id));
    if (fresh.length === 0) return;
    onAdd(fresh);
    setPickedIds([]);
  };

  const columns: ColumnsType<Question> = [
    {
      title: '题干',
      dataIndex: 'stem',
      render: (stem: string) => (
        <Typography.Text ellipsis={{ tooltip: stem }} style={{ maxWidth: 320 }}>
          {stem}
        </Typography.Text>
      ),
    },
    {
      title: '题型',
      dataIndex: 'type',
      width: 92,
      render: (value: QuestionType) => (
        <Tag color={QUESTION_TYPE_COLORS[value]}>{QUESTION_TYPE_LABELS[value]}</Tag>
      ),
    },
    {
      title: '操作',
      key: 'add',
      width: 88,
      render: (_value: unknown, record: Question) =>
        addedSet.has(record.id) ? (
          <Tag>已加入</Tag>
        ) : (
          <Button
            type="link"
            size="small"
            disabled={readOnly}
            onClick={() => {
              addQuestions([record]);
            }}
            data-testid={`papers-add-${record.id}`}
          >
            加入
          </Button>
        ),
    },
  ];

  return (
    <Card
      title={
        <Space align="center">
          <Typography.Text strong>题库选题</Typography.Text>
          <Typography.Text type="secondary">共 {total} 题</Typography.Text>
        </Space>
      }
      data-testid="papers-picker"
    >
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        <Space wrap align="center">
          <Input
            allowClear
            value={keywordDraft}
            placeholder="搜索题干关键词"
            maxLength={100}
            style={{ width: 180 }}
            onChange={(event) => {
              setKeywordDraft(event.target.value);
            }}
            onPressEnter={applySearch}
            data-testid="papers-keyword"
          />
          <Input
            allowClear
            value={tagDraft}
            placeholder="标签筛选"
            maxLength={32}
            style={{ width: 130 }}
            onChange={(event) => {
              setTagDraft(event.target.value);
            }}
            onPressEnter={applySearch}
            data-testid="papers-tag-filter"
          />
          <Button type="primary" onClick={applySearch} data-testid="papers-search">
            搜索
          </Button>
          <Button onClick={resetFilters} data-testid="papers-reset">
            重置
          </Button>
        </Space>

        <Space wrap align="center">
          <Radio.Group
            value={type ?? ''}
            onChange={(event) => {
              const value = event.target.value as QuestionType | '';
              setType(value === '' ? undefined : value);
              setPage(1);
            }}
          >
            <Radio.Button value="">全部</Radio.Button>
            {QUESTION_TYPES.map((item) => (
              <Radio.Button key={item} value={item}>
                {QUESTION_TYPE_LABELS[item]}
              </Radio.Button>
            ))}
          </Radio.Group>
          <Select
            value={pageSize}
            style={{ width: 100 }}
            options={PAGE_SIZE_OPTIONS.map((size) => ({ value: size, label: `${size} 条` }))}
            onChange={(value: number) => {
              setPageSize(value);
              setPage(1);
            }}
          />
        </Space>

        {listError ? (
          <div data-testid="papers-picker-error">
            <Alert type="error" showIcon message={`加载题库失败：${listError}`} />
          </div>
        ) : null}

        <Table<Question>
          rowKey="id"
          size="small"
          loading={questionsQuery.isLoading}
          dataSource={items}
          columns={columns}
          locale={{ emptyText: '题库暂无匹配题目' }}
          rowSelection={{
            selectedRowKeys: pickedIds,
            onChange: (keys) => {
              setPickedIds(keys.map((key) => Number(key)));
            },
            getCheckboxProps: (record: Question) => ({
              disabled: readOnly || addedSet.has(record.id),
            }),
          }}
          pagination={{
            current: page,
            pageSize,
            total,
            size: 'small',
            showSizeChanger: false,
            showTotal: (count) => `共 ${count} 题`,
            onChange: (nextPage) => {
              setPage(nextPage);
            },
          }}
        />

        <Space align="center">
          <Button
            type="primary"
            disabled={readOnly || pickedQuestions.length === 0}
            onClick={() => {
              addQuestions(pickedQuestions);
            }}
            data-testid="papers-add-selected"
          >
            加入已选（{pickedQuestions.length}）
          </Button>
          <Typography.Text type="secondary">已加入 {addedIds.length} 题</Typography.Text>
        </Space>
      </Space>
    </Card>
  );
}
