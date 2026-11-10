import { Button, Result } from 'antd';
import { useNavigate } from 'react-router-dom';

/** 404：未匹配路由的统一落点。 */
export default function NotFound() {
  const navigate = useNavigate();

  return (
    <div data-testid="not-found" style={{ padding: 48 }}>
      <Result
        status="404"
        title="404"
        subTitle="页面不存在，或链接已失效。"
        extra={
          <Button
            type="primary"
            data-testid="not-found-home"
            onClick={() => {
              navigate('/');
            }}
          >
            返回工作台
          </Button>
        }
      />
    </div>
  );
}
