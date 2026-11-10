import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Modal, Space, Table, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';

import type { ImportFailure, ImportResult } from './model';

const ACCEPTED_EXTENSIONS = ['.csv', '.xlsx'] as const;

const FAILED_COLUMNS: ColumnsType<ImportFailure> = [
  { title: '行号', dataIndex: 'row', width: 100 },
  { title: '失败原因', dataIndex: 'reason' },
];

function ImportResultView({ result }: { result: ImportResult }) {
  return (
    <div data-testid="questions-import-result">
      <Space size={16} wrap style={{ marginBottom: 12 }}>
        <span data-testid="questions-import-total">文件行数：{result.total}</span>
        <span data-testid="questions-import-imported">成功导入：{result.imported}</span>
        <span data-testid="questions-import-skipped">重复跳过：{result.skipped_duplicates}</span>
        <span data-testid="questions-import-failed">失败行数：{result.failed.length}</span>
      </Space>
      {result.failed.length > 0 ? (
        <Table<ImportFailure>
          size="small"
          rowKey={(row) => `${row.row}-${row.reason}`}
          columns={FAILED_COLUMNS}
          dataSource={result.failed}
          pagination={false}
        />
      ) : (
        <Alert type="success" showIcon message="全部行导入成功" />
      )}
    </div>
  );
}

export interface ImportQuestionsModalProps {
  open: boolean;
  importing: boolean;
  /** 导入结果（HTTP 恒 200，可能部分成功）；未导入时为 null。 */
  result: ImportResult | null;
  errorText: string | null;
  onCancel: () => void;
  onSubmit: (file: File) => void;
}

/**
 * 批量导入弹窗：上传 CSV / XLSX（multipart 字段名 `file`），
 * 展示 ImportResult 的 imported / failed[{row,reason}] / total / skipped_duplicates。
 */
export default function ImportQuestionsModal({
  open,
  importing,
  result,
  errorText,
  onCancel,
  onSubmit,
}: ImportQuestionsModalProps) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setFile(null);
    setFileError(null);
    if (inputRef.current) inputRef.current.value = '';
  }, [open]);

  const handleSelect = (files: FileList | null): void => {
    const picked = files && files.length > 0 ? files[0] : null;
    if (!picked) {
      setFile(null);
      return;
    }
    if (!ACCEPTED_EXTENSIONS.some((extension) => picked.name.toLowerCase().endsWith(extension))) {
      setFile(null);
      setFileError('仅支持 .csv 或 .xlsx 文件');
      if (inputRef.current) inputRef.current.value = '';
      return;
    }
    setFileError(null);
    setFile(picked);
  };

  return (
    <Modal
      open={open}
      title="批量导入题目"
      width={760}
      onCancel={onCancel}
      maskClosable={!importing}
      keyboard={!importing}
      footer={
        <Space>
          <Button onClick={onCancel} disabled={importing}>
            关闭
          </Button>
          <Button
            type="primary"
            loading={importing}
            disabled={!file}
            onClick={() => {
              if (file && !importing) onSubmit(file);
            }}
            data-testid="questions-import-submit"
          >
            开始导入
          </Button>
        </Space>
      }
    >
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
          支持 CSV / XLSX，需包含题型、题干、选项、答案、解析、难度、标签列。导入按「题干 + 题型」
          幂等去重，重复行自动跳过；失败行会列出行号与原因。
        </Typography.Paragraph>

        <Space wrap align="center">
          <input
            ref={inputRef}
            type="file"
            accept=".csv,.xlsx"
            style={{ display: 'none' }}
            data-testid="questions-import-file"
            onChange={(event) => {
              handleSelect(event.target.files);
            }}
          />
          <Button
            onClick={() => {
              inputRef.current?.click();
            }}
            data-testid="questions-import-select"
          >
            选择文件
          </Button>
          <Typography.Text type="secondary">{file ? file.name : '未选择文件'}</Typography.Text>
        </Space>

        {fileError ? (
          <div data-testid="questions-import-file-error">
            <Alert type="error" showIcon message={fileError} />
          </div>
        ) : null}

        {errorText ? (
          <div data-testid="questions-import-error">
            <Alert type="error" showIcon message={errorText} />
          </div>
        ) : null}

        {result ? <ImportResultView result={result} /> : null}
      </Space>
    </Modal>
  );
}
