import { useEffect } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { RouterProvider } from 'react-router-dom';

import { loginRedirectUrl, setUnauthorizedHandler } from './api/client';
import { router } from './routes';

/** 全局 QueryClient：401 由请求层统一处理，这里只控制重试与缓存新鲜度。 */
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
      staleTime: 30_000,
    },
    mutations: {
      retry: 0,
    },
  },
});

export default function App() {
  // 把请求层的 401 兜底接到 router，避免整页刷新丢失 SPA 状态。
  useEffect(() => {
    setUnauthorizedHandler(({ returnTo }) => {
      if (router.state.location.pathname === '/login') return;
      void router.navigate(loginRedirectUrl(returnTo), { replace: true });
    });
    return () => {
      setUnauthorizedHandler(null);
    };
  }, []);

  return (
    <ConfigProvider
      locale={zhCN}
      theme={{
        token: {
          colorPrimary: '#1677ff',
          borderRadius: 6,
        },
      }}
    >
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </ConfigProvider>
  );
}
