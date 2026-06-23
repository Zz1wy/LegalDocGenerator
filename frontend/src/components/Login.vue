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
import { ref, reactive } from 'vue'
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

const rules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }]
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
    alert('操作失败，请检查网络')
  } finally {
    loading.value = false
  }
}
</script>