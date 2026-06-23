import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],

  // 开发服务器配置
  server: {
    port: 5173,
    // 代理配置：开发模式将 /api 请求转发到后端
    // 这样前端代码统一用 /api/... 相对路径，开发和生产都能工作
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/health': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      }
    }
  }
})
