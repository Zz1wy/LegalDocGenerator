<template>
  <div style="height: 100vh; display: flex; align-items: center; justify-content: center; background: linear-gradient(135deg, #1e3a8a 0%, #2563eb 100%);">
    <el-card style="width: 400px; border-radius: 12px; box-shadow: 0 10px 40px rgba(0,0,0,0.2);">
      <template #header>
        <div style="text-align: center;">
          <h2 style="margin: 0; color: #1e3a8a;">⚖️ 法律文书系统</h2>
          <p style="margin: 5px 0 0; color: #64748b; font-size: 14px;">{{ isLogin ? '欢迎回来' : '创建新账号' }}</p>
        </div>
      </template>

      <el-form :model="form" :rules="rules" ref="formRef">
        <el-form-item prop="username">
          <el-input v-model="form.username" placeholder="用户名" prefix-icon="User" />
        </el-form-item>
        <el-form-item prop="password">
          <el-input v-model="form.password" type="password" placeholder="密码" prefix-icon="Lock" show-password />
        </el-form-item>
        <el-form-item v-if="!isLogin" prop="nickname">
          <el-input v-model="form.nickname" placeholder="昵称（可选）" prefix-icon="UserFilled" />
        </el-form-item>
      </el-form>

      <el-button 
        type="primary" 
        style="width: 100%; margin-top: 10px;" 
        :loading="loading"
        @click="handleSubmit"
      >
        {{ isLogin ? '登录' : '注册' }}
      </el-button>

      <div style="text-align: center; margin-top: 15px;">
        <el-button type="text" @click="isLogin = !isLogin">
          {{ isLogin ? '没有账号？去注册' : '已有账号？去登录' }}
        </el-button>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, reactive, computed } from 'vue'
import axios from 'axios'

const emit = defineEmits(['login-success'])

const isLogin = ref(true)
const loading = ref(false)
const formRef = ref(null)

const form = reactive({
  username: '',
  password: '',
  nickname: ''
})

// 只做「注册」时的长度校验；登录不限制下限，避免锁死历史上用短密码注册的账号。
function validatePasswordBytes(rule, value, callback) {
  if (value && new TextEncoder().encode(value).length > 72) {
    callback(new Error('密码不能超过 72 字节'))
  } else {
    callback()
  }
}

const rules = computed(() => ({
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' },
    { min: 2, max: 50, message: '用户名长度需为 2–50 个字符', trigger: 'blur' }
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    ...(isLogin.value
      ? []
      : [
          { min: 8, message: '密码至少 8 个字符', trigger: 'blur' },
          { validator: validatePasswordBytes, trigger: 'blur' }
        ])
  ],
  nickname: [{ max: 50, message: '昵称最多 50 个字符', trigger: 'blur' }]
}))

const FIELD_LABELS = { username: '用户名', password: '密码', nickname: '昵称' }
const VALIDATION_MESSAGES = {
  'String should have at least 8 characters': '至少需要 8 个字符',
  'String should have at least 2 characters': '至少需要 2 个字符',
  'String should have at most 50 characters': '最多 50 个字符',
  'Field required': '不能为空'
}

// 后端 422 的 detail 是数组；401 等错误是字符串。把真正的原因显示出来，
// 不要一律提示“检查网络”。
function formatError(error) {
  const data = error?.response?.data
  const detail = data?.detail
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        const field = FIELD_LABELS[item.loc?.[item.loc.length - 1]] || ''
        let msg = item.msg || item.type || '参数不合法'
        msg = msg.replace(/^Value error, /, '')
        msg = VALIDATION_MESSAGES[msg] || msg
        // 后端自定义错误可能已带字段名（如“密码不能超过 72 字节”），避免重复
        return field && !msg.startsWith(field) ? `${field}${msg}` : msg
      })
      .join('；')
  }
  if (typeof detail === 'string') return detail
  // 限流等错误返回的是 { success: false, message: "..." }
  if (typeof data?.message === 'string') return data.message
  if (error?.response) return `请求失败（HTTP ${error.response.status}）`
  return '无法连接服务器，请确认后端已启动'
}

async function handleSubmit() {
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return

  loading.value = true
  try {
    const url = isLogin.value ? '/api/login' : '/api/register'
    const response = await axios.post(url, {
      username: form.username,
      password: form.password,
      nickname: form.nickname || undefined
    })

    if (response.data.success) {
      // 保存 token
      localStorage.setItem('token', response.data.token)
      localStorage.setItem('user', JSON.stringify(response.data.user))
      emit('login-success', response.data.user)
    } else {
      alert(response.data.message)
    }
  } catch (error) {
    console.error('操作失败:', error)
    alert(formatError(error))
  } finally {
    loading.value = false
  }
}
</script>