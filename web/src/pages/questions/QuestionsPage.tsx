import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Alert,
  Button,
  Card,
  Input,
  Modal,
  Radio,
  Rate,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';

import { queryKeys } from '../../api/client';
import { createQuestion, deleteQuestion, importQuestions, listQuestions, updateQuestion } from './api';
import ImportQuestionsModal from './ImportQuestionsModal';
import QuestionFormModal from './QuestionFormModal';
import {
  MAX_DIFFICULTY,
  QUESTION_TYPE_COLORS,
  QUESTION_TYPE_LABELS,
  QUESTION_TYPES,
  buildQuestionPayload,
  describeQuestionApiError,
} from './model';
import type {
  ImportResult,
  Question,
  QuestionCreate,
  QuestionFormValues,
  QuestionType,
} from './model';

const PAGE_SIZE_OPTIONS = [10, 20, 50, 100];

/**
 * 题库管理页（TASK-FE-002）。
 *
 * - 列表：GET /questions（page/page_size/keyword/type/tag），契约 QuestionPage
 * - 新建/编辑：POST /questions、PUT /questions/{id}（QuestionCreate）
 * - 删除：DELETE /questions/{id}，二次确认；409 QUESTION_IN_USE → 「题目已被试卷引用」
 * - 导入：POST /questions/import（multipart file），展示 ImportResult 明细
 * - 缓存：所有写操作成功后失效 `['questions']` 前缀（queryKeys.questions 的所有筛选组合）
 *
 * data-testid：page-questions、questions-create、questions-submit、questions-import-file、
 * questions-row-{id}、questions-delete-{id}
 */
export default function QuestionsPage() {
  const queryClient = useQueryClient();

  // 筛选/分页状态：输入草稿与应用值分离，避免每次键入都打后端。
  const [keywordDraft, setKeywordDraft] = useState('');
  const [tagDraft, setTagDraft] = useState('');
  const [keyword, setKeyword] = useState('');
  const [tag, setTag] = useState('');
  const [type, setType] = useState<QuestionType | undefined>(undefined);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);

  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<Question | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  const [importOpen, setImportOpen] = useState(false);
  const [importResult, setImportResult] = useState<ImportResult | null>(null);
  const [importError, setImportError] = useState<string | null>(null);

  const [deleting, setDeleting] = useState<Question | null>(null);
  const [pageError, setPageError] = useState<string | null>(null);

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

  /** `['questions']` 是 queryKeys.questions(...) 所有组合的前缀，一次失效全部列表缓存。 */
  const invalidateQuestions = (): void => {
    void queryClient.invalidateQueries({ queryKey: ['questions'] });
  };

  const saveMutation = useMutation({
    mutationFn: (input: { id: number | null; payload: QuestionCreate }) =>
      input.id === null ? createQuestion(input.payload) : updateQuestion(input.id, input.payload),
    onSuccess: () => {
      setFormOpen(false);
      setEditing(null);
      setFormError(null);
      invalidateQuestions();
      message.success('保存成功');
    },
    onError: (error) => {
      setFormError(describeQuestionApiError(error, '保存失败，请稍后重试'));
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => deleteQuestion(id),
    onSuccess: () => {
      setDeleting(null);
      setPageError(null);
      invalidateQuestions();
      message.success('题目已删除');
    },
    onError: (error) => {
      setDeleting(null);
      setPageError(describeQuestionApiError(error, '删除失败，请稍后重试'));
    },
  });

  const importMutation = useMutation({
    mutationFn: (file: File) => importQuestions(file),
    onSuccess: (result) => {
      setImportResult(result);
      setImportError(null);
      invalidateQuestions();
      if (result.imported > 0) message.success(`成功导入 ${result.imported} 题`);
    },
    onError: (error) => {
      setImportError(describeQuestionApiError(error, '导入失败，请稍后重试'));
    },
  });

  const items = questionsQuery.data?.items ?? [];
  const total = questionsQuery.data?.total ?? 0;
  const listError = questionsQuery.isError
    ? describeQuestionApiError(questionsQuery.error, '请稍后重试')
    : null;

  const openCreate = (): void => {
    setEditing(null);
    setFormError(null);
    setFormOpen(true);
  };

  const openEdit = (question: Question): void => {
    setEditing(question);
    setFormError(null);
    setFormOpen(true);
  };

  const closeForm = (): void => {
    if (saveMutation.isPending) return;
    setFormOpen(false);
    setFormError(null);
  };

  const openImport = (): void => {
    setImportResult(null);
    setImportError(null);
    setImportOpen(true);
  };

  const closeImport = (): void => {
    if (importMutation.isPending) return;
    setImportOpen(false);
  };

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

  const submitForm = (values: QuestionFormValues): void => {
    setFormError(null);
    saveMutation.mutate({ id: editing?.id ?? null, payload: buildQuestionPayload(values) });
  };

  const confirmDelete = (): void => {
    if (!deleting || deleteMutation.isPending) return;
    deleteMutation.mutate(deleting.id);
  };

  const columns: ColumnsType<Question> = [
    {
      title: '题干',
      dataIndex: 'stem',
      render: (stem: string, record: Question) => (
        <div data-testid={`questions-row-${record.id}`} style={{ maxWidth: 460 }}>
          <Typography.Text ellipsis={{ tooltip: stem }} style={{ maxWidth: 440 }}>
            {stem}
          </Typography.Text>
        </div>
      ),
    },
    {
      title: '题型',
      dataIndex: 'type',
      width: 100,
      render: (value: QuestionType) => (
        <Tag color={QUESTION_TYPE_COLORS[value]}>{QUESTION_TYPE_LABELS[value]}</Tag>
      ),
    },
    {
      title: '难度',
      dataIndex: 'difficulty',
      width: 150,
      render: (value: number) => (
        <Space size={4}>
          <Rate disabled count={MAX_DIFFICULTY} value={value} style={{ fontSize: 14 }} />
          <Typography.Text type="secondary">
            {value}/{MAX_DIFFICULTY}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: '标签',
      dataIndex: 'tags',
      width: 180,
      render: (tags: string[]) => (
        <Space size={[0, 4]} wrap>
          {tags.length === 0 ? (
            <Typography.Text type="secondary">—</Typography.Text>
          ) : (
            tags.map((item) => <Tag key={item}>{item}</Tag>)
          )}
        </Space>
      ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 130,
      render: (_value: unknown, record: Question) => (
        <Space size="small">
          <Button
            type="link"
            size="small"
            onClick={() => {
              openEdit(record);
            }}
            data-testid={`questions-edit-${record.id}`}
          >
            编辑
          </Button>
          <Button
            type="link"
            size="small"
            danger
            onClick={() => {
              setDeleting(record);
            }}
            data-testid={`questions-delete-${record.id}`}
          >
            删除
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <div data-testid="page-questions">
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <Card
          title={
            <Space align="center">
              <Typography.Title level={4} style={{ margin: 0 }}>
                题库管理
              </Typography.Title>
              <Typography.Text type="secondary">题库共 {total} 题</Typography.Text>
            </Space>
          }
          extra={
            <Space>
              <Button onClick={openImport} data-testid="questions-import">
                批量导入
              </Button>
              <Button type="primary" onClick={openCreate} data-testid="questions-create">
                新建题目
              </Button>
            </Space>
          }
        >
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Space wrap align="center">
              <Input
                allowClear
                value={keywordDraft}
                placeholder="搜索题干关键词"
                maxLength={100}
                style={{ width: 220 }}
                onChange={(event) => {
                  setKeywordDraft(event.target.value);
                }}
                onPressEnter={applySearch}
                data-testid="questions-keyword"
              />
              <Input
                allowClear
                value={tagDraft}
                placeholder="标签筛选"
                maxLength={32}
                style={{ width: 150 }}
                onChange={(event) => {
                  setTagDraft(event.target.value);
                }}
                onPressEnter={applySearch}
                data-testid="questions-tag-filter"
              />
              <Button type="primary" onClick={applySearch} data-testid="questions-search">
                搜索
              </Button>
              <Button onClick={resetFilters} data-testid="questions-reset">
                重置
              </Button>
            </Space>

            <Space wrap align="center">
              <Typography.Text type="secondary">题型</Typography.Text>
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
              <Typography.Text type="secondary">每页</Typography.Text>
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
          </Space>
        </Card>

        {pageError ? (
          <div data-testid="questions-error">
            <Alert
              type="error"
              showIcon
              closable
              message={pageError}
              onClose={() => {
                setPageError(null);
              }}
            />
          </div>
        ) : null}

        {listError ? (
          <div data-testid="questions-list-error">
            <Alert
              type="error"
              showIcon
              message={`加载题库失败：${listError}`}
              action={
                <Button
                  size="small"
                  onClick={() => {
                    void questionsQuery.refetch();
                  }}
                >
                  重试
                </Button>
              }
            />
          </div>
        ) : null}

        <Card>
          <Table<Question>
            rowKey="id"
            size="middle"
            loading={questionsQuery.isLoading}
            dataSource={items}
            columns={columns}
            locale={{ emptyText: '暂无题目，点击「新建题目」开始录入' }}
            pagination={{
              current: page,
              pageSize,
              total,
              showSizeChanger: false,
              showTotal: (count) => `共 ${count} 题`,
              onChange: (nextPage) => {
                setPage(nextPage);
              },
            }}
          />
        </Card>
      </Space>

      <QuestionFormModal
        open={formOpen}
        mode={editing ? 'edit' : 'create'}
        question={editing}
        submitting={saveMutation.isPending}
        errorText={formError}
        onCancel={closeForm}
        onSubmit={submitForm}
      />

      <ImportQuestionsModal
        open={importOpen}
        importing={importMutation.isPending}
        result={importResult}
        errorText={importError}
        onCancel={closeImport}
        onSubmit={(file) => {
          importMutation.mutate(file);
        }}
      />

      <Modal
        open={deleting !== null}
        title="删除题目"
        onCancel={() => {
          if (!deleteMutation.isPending) setDeleting(null);
        }}
        maskClosable={!deleteMutation.isPending}
        footer={
          <Space>
            <Button
              disabled={deleteMutation.isPending}
              onClick={() => {
                setDeleting(null);
              }}
            >
              取消
            </Button>
            <Button
              type="primary"
              danger
              loading={deleteMutation.isPending}
              onClick={confirmDelete}
              data-testid="questions-delete-confirm"
            >
              确认删除
            </Button>
          </Space>
        }
      >
        <Typography.Paragraph>
          确定要删除题目「{deleting?.stem ?? ''}」吗？此操作需要二次确认。
        </Typography.Paragraph>
        <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
          删除为软删除；若题目已被试卷引用，将无法删除。
        </Typography.Paragraph>
      </Modal>
    </div>
  );
}
