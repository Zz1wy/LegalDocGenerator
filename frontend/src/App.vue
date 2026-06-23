<template>
  <div id="app">
    <!-- 未登录显示登录页 -->
    <Login v-if="!isLoggedIn" @login-success="onLoginSuccess" />
    
    <!-- 已登录显示主应用 -->
    <el-container style="min-height: 100vh;">
      <el-header style="background: linear-gradient(135deg, #1e3a8a 0%, #2563eb 100%); color: white; line-height: 60px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
        <div style="display: flex; align-items: center; justify-content: space-between;">
          <h1 style="margin: 0; font-size: 20px; font-weight: 600;">⚖️ 智能法律文书辅助生成系统</h1>
          <div style="display: flex; align-items: center; gap: 20px;">
            <span style="font-size: 14px; opacity: 0.9;">专业 · 高效 · 安全</span>
            <!-- 用户信息和退出按钮 -->
            <div style="display: flex; align-items: center; gap: 15px;">
              <span style="font-size: 14px;">👤 {{ currentUser?.nickname || currentUser?.username }}</span>
              <el-button type="danger" size="small" @click="logout">🚪 退出登录</el-button>
            </div>
          </div>
        </div>
      </el-header>

      <el-main style="background: linear-gradient(135deg, #f5f7fa 0%, #e4e8ec 100%); padding: 20px;">
        <transition name="fade">
          <el-alert v-if="errorMsg" :title="errorMsg" type="error" style="margin-bottom: 20px;" />
        </transition>

        <el-tabs v-model="activeTab" type="card" style="background: white; border-radius: 8px; overflow: hidden;">
          <!-- 生成页面 -->
          <el-tab-pane label="📝 文书生成" name="generate">
            <el-row :gutter="20" style="margin-top: 20px;">
              <el-col :span="8">
                <el-card style="margin-bottom: 20px;">
                  <template #header>
                    <span style="font-weight: 600;">📋 选择文书类型</span>
                  </template>
                  <el-radio-group v-model="selectedDocType">
                    <el-radio 
                      v-for="doc in docTypes" 
                      :key="doc.id" 
                      :value="doc.name" 
                      style="display: block; margin-bottom: 10px; padding: 8px; border-radius: 6px;"
                    >
                      <span>{{ doc.name }}</span>
                      <span style="float: right; font-size: 12px; color: #64748b; background: #f1f5f9; padding: 2px 8px; border-radius: 10px;">{{ doc.category }}</span>
                    </el-radio>
                  </el-radio-group>
                </el-card>

                <el-card>
                  <template #header>
                    <span style="font-weight: 600;">✏️ 填写信息</span>
                  </template>
                  <el-form :model="formData" label-width="100px">
                    <!-- 劳动合同字段 -->
                    <el-form-item v-if="selectedDocType === '劳动合同'" label="甲方名称">
                      <el-input v-model="formData.party_a" placeholder="请输入甲方名称" />
                    </el-form-item>
                    <el-form-item v-if="selectedDocType === '劳动合同'" label="乙方姓名">
                      <el-input v-model="formData.party_b" placeholder="请输入乙方姓名" />
                    </el-form-item>
                    <el-form-item v-if="selectedDocType === '劳动合同'" label="合同期限">
                      <el-input v-model="formData.term" placeholder="如：三年" />
                    </el-form-item>
                    <el-form-item v-if="selectedDocType === '劳动合同'" label="工资待遇">
                      <el-input v-model="formData.salary" placeholder="请输入月薪" />
                    </el-form-item>

                    <!-- 民事起诉状字段 -->
                    <el-form-item v-if="selectedDocType === '民事起诉状'" label="原告姓名">
                      <el-input v-model="formData.plaintiff" placeholder="请输入原告姓名" />
                    </el-form-item>
                    <el-form-item v-if="selectedDocType === '民事起诉状'" label="被告姓名">
                      <el-input v-model="formData.defendant" placeholder="请输入被告姓名" />
                    </el-form-item>
                    <el-form-item v-if="selectedDocType === '民事起诉状'" label="诉讼请求">
                      <el-input type="textarea" v-model="formData.claim" :rows="2" placeholder="请输入诉讼请求" />
                    </el-form-item>
                    <el-form-item v-if="selectedDocType === '民事起诉状'" label="事实与理由">
                      <el-input type="textarea" v-model="formData.reason" :rows="2" placeholder="请输入事实与理由" />
                    </el-form-item>

                    <!-- 借条字段 -->
                    <el-form-item v-if="selectedDocType === '借条'" label="出借人">
                      <el-input v-model="formData.lender" placeholder="请输入出借人姓名" />
                    </el-form-item>
                    <el-form-item v-if="selectedDocType === '借条'" label="借款人">
                      <el-input v-model="formData.borrower" placeholder="请输入借款人姓名" />
                    </el-form-item>
                    <el-form-item v-if="selectedDocType === '借条'" label="借款金额">
                      <el-input v-model="formData.amount" placeholder="请输入借款金额" />
                    </el-form-item>
                    <el-form-item v-if="selectedDocType === '借条'" label="还款日期">
                      <el-input v-model="formData.repay_date" placeholder="请输入还款日期" />
                    </el-form-item>

                    <!-- 租赁合同字段 -->
                    <el-form-item v-if="selectedDocType === '租赁合同'" label="出租方">
                      <el-input v-model="formData.landlord" placeholder="请输入出租方姓名" />
                    </el-form-item>
                    <el-form-item v-if="selectedDocType === '租赁合同'" label="承租方">
                      <el-input v-model="formData.tenant" placeholder="请输入承租方姓名" />
                    </el-form-item>
                    <el-form-item v-if="selectedDocType === '租赁合同'" label="租赁物">
                      <el-input v-model="formData.property" placeholder="请输入租赁物" />
                    </el-form-item>
                    <el-form-item v-if="selectedDocType === '租赁合同'" label="租金">
                      <el-input v-model="formData.rent" placeholder="请输入租金" />
                    </el-form-item>
                    <el-form-item v-if="selectedDocType === '租赁合同'" label="租期">
                      <el-input v-model="formData.term" placeholder="请输入租期" />
                    </el-form-item>

                    <!-- 离婚协议书字段 -->
                    <el-form-item v-if="selectedDocType === '离婚协议书'" label="男方姓名">
                      <el-input v-model="formData.husband" placeholder="请输入男方姓名" />
                    </el-form-item>
                    <el-form-item v-if="selectedDocType === '离婚协议书'" label="女方姓名">
                      <el-input v-model="formData.wife" placeholder="请输入女方姓名" />
                    </el-form-item>
                    <el-form-item v-if="selectedDocType === '离婚协议书'" label="子女抚养">
                      <el-input type="textarea" v-model="formData.child_custody" :rows="2" placeholder="请输入子女抚养权归属" />
                    </el-form-item>
                    <el-form-item v-if="selectedDocType === '离婚协议书'" label="财产分割">
                      <el-input type="textarea" v-model="formData.property_split" :rows="2" placeholder="请输入财产分割方案" />
                    </el-form-item>

                    <!-- 股权转让协议字段 -->
                    <el-form-item v-if="selectedDocType === '股权转让协议'" label="转让方">
                      <el-input v-model="formData.transferor" placeholder="请输入转让方姓名" />
                    </el-form-item>
                    <el-form-item v-if="selectedDocType === '股权转让协议'" label="受让方">
                      <el-input v-model="formData.transferee" placeholder="请输入受让方姓名" />
                    </el-form-item>
                    <el-form-item v-if="selectedDocType === '股权转让协议'" label="公司名称">
                      <el-input v-model="formData.company_name" placeholder="请输入目标公司名称" />
                    </el-form-item>
                    <el-form-item v-if="selectedDocType === '股权转让协议'" label="股权比例">
                      <el-input v-model="formData.share_ratio" placeholder="如：50%" />
                    </el-form-item>
                    <el-form-item v-if="selectedDocType === '股权转让协议'" label="转让价格">
                      <el-input v-model="formData.share_price" placeholder="请输入转让价格" />
                    </el-form-item>

                    <!-- 保密协议字段 -->
                    <el-form-item v-if="selectedDocType === '保密协议'" label="甲方">
                      <el-input v-model="formData.employer" placeholder="请输入甲方名称" />
                    </el-form-item>
                    <el-form-item v-if="selectedDocType === '保密协议'" label="乙方">
                      <el-input v-model="formData.employee" placeholder="请输入乙方名称" />
                    </el-form-item>
                    <el-form-item v-if="selectedDocType === '保密协议'" label="保密范围">
                      <el-input v-model="formData.confidential_scope" placeholder="请输入保密信息范围" />
                    </el-form-item>
                    <el-form-item v-if="selectedDocType === '保密协议'" label="保密期限">
                      <el-input v-model="formData.confidential_period" placeholder="如：两年" />
                    </el-form-item>

                    <!-- 房屋买卖合同字段 -->
                    <el-form-item v-if="selectedDocType === '房屋买卖合同'" label="卖方">
                      <el-input v-model="formData.seller" placeholder="请输入卖方姓名" />
                    </el-form-item>
                    <el-form-item v-if="selectedDocType === '房屋买卖合同'" label="买方">
                      <el-input v-model="formData.buyer" placeholder="请输入买方姓名" />
                    </el-form-item>
                    <el-form-item v-if="selectedDocType === '房屋买卖合同'" label="房屋地址">
                      <el-input v-model="formData.house_address" placeholder="请输入房屋地址" />
                    </el-form-item>
                    <el-form-item v-if="selectedDocType === '房屋买卖合同'" label="房屋面积">
                      <el-input v-model="formData.house_area" placeholder="如：120平方米" />
                    </el-form-item>
                    <el-form-item v-if="selectedDocType === '房屋买卖合同'" label="成交价格">
                      <el-input v-model="formData.house_price" placeholder="请输入成交价格" />
                    </el-form-item>

                    <!-- 委托代理书字段 -->
                    <el-form-item v-if="selectedDocType === '委托代理书'" label="委托人">
                      <el-input v-model="formData.client" placeholder="请输入委托人姓名" />
                    </el-form-item>
                    <el-form-item v-if="selectedDocType === '委托代理书'" label="受托人">
                      <el-input v-model="formData.agent" placeholder="请输入受托人姓名" />
                    </el-form-item>
                    <el-form-item v-if="selectedDocType === '委托代理书'" label="代理事项">
                      <el-input v-model="formData.matter" placeholder="请输入代理事项" />
                    </el-form-item>
                    <el-form-item v-if="selectedDocType === '委托代理书'" label="代理权限">
                      <el-input v-model="formData.authority" placeholder="请输入代理权限范围" />
                    </el-form-item>
                    <el-form-item v-if="selectedDocType === '委托代理书'" label="代理期限">
                      <el-input v-model="formData.period" placeholder="如：一年" />
                    </el-form-item>
                  </el-form>

                  <el-button 
                    type="primary" 
                    @click="generateDoc" 
                    style="width: 100%; margin-top: 20px;"
                    :loading="isLoading"
                  >
                    {{ isLoading ? '⏳ 生成中...' : '🚀 生成文书' }}
                  </el-button>
                </el-card>
              </el-col>

              <el-col :span="16">
                <el-card>
                  <template #header>
                    <span style="font-weight: 600;">📄 生成结果</span>
                  </template>
                  <el-scrollbar style="height: 450px; background-color: #fafafa; border-radius: 6px; padding: 15px;">
                    <transition name="fade">
                      <pre v-if="generatedContent" style="margin: 0; white-space: pre-wrap; font-family: 'SimSun', 'Songti SC', serif; font-size: 14px; line-height: 1.8; color: #1e293b;">{{ generatedContent }}</pre>
                      <div v-else style="text-align: center; padding: 100px 20px; color: #94a3b8;">
                        <div style="font-size: 48px; margin-bottom: 20px;">📝</div>
                        <p style="font-size: 16px;">请选择文书类型并填写信息，然后点击"生成文书"</p>
                      </div>
                    </transition>
                  </el-scrollbar>
                  <el-button type="success" @click="exportDoc" style="margin-top: 20px;" :disabled="!generatedContent">
                    📥 导出文档
                  </el-button>
                </el-card>
              </el-col>
            </el-row>
          </el-tab-pane>

          <!-- 历史记录页面 -->
          <el-tab-pane label="📜 历史记录" name="history">
            <el-card style="margin-top: 20px;">
              <template #header>
                <span style="font-weight: 600;">📋 生成历史记录</span>
                <el-button type="primary" size="small" @click="loadHistory" style="float: right;">
                  🔄 刷新记录
                </el-button>
              </template>

              <!-- 搜索筛选区域 -->
              <div style="margin-bottom: 20px; padding: 15px; background: #f8fafc; border-radius: 8px;">
                <el-row :gutter="15">
                  <el-col :span="8">
                    <el-input
                      v-model="searchKeyword"
                      placeholder="🔍 搜索文书类型或内容..."
                      clearable
                      @input="filterRecords"
                    >
                    </el-input>
                  </el-col>
                  <el-col :span="6">
                    <el-date-picker
                      v-model="dateRange"
                      type="daterange"
                      range-separator="至"
                      start-placeholder="开始日期"
                      end-placeholder="结束日期"
                      value-format="YYYY-MM-DD"
                      @change="filterRecords"
                      style="width: 100%;"
                    />
                  </el-col>
                  <el-col :span="4">
                    <el-select v-model="filterDocType" placeholder="全部类型" clearable @change="filterRecords">
                      <el-option 
                        v-for="doc in docTypes" 
                        :key="doc.id" 
                        :label="doc.name" 
                        :value="doc.name" 
                      />
                    </el-select>
                  </el-col>
                  <el-col :span="6">
                    <div style="line-height: 32px; color: #64748b; font-size: 14px;">
                      共 <span style="color: #2563eb; font-weight: bold;">{{ filteredRecords.length }}</span> 条记录
                    </div>
                  </el-col>
                </el-row>
              </div>

              <!-- 统计卡片 -->
              <div style="margin-bottom: 20px;">
                <el-row :gutter="10">
                  <el-col :span="4" v-for="(count, type) in docTypeStats" :key="type">
                    <div style="text-align: center; padding: 10px; background: linear-gradient(135deg, #dbeafe 0%, #bfdbfe 100%); border-radius: 8px;">
                      <div style="font-size: 24px; font-weight: bold; color: #1d4ed8;">{{ count }}</div>
                      <div style="font-size: 12px; color: #64748b;">{{ type }}</div>
                    </div>
                  </el-col>
                </el-row>
              </div>

              <!-- 记录列表 -->
              <transition name="fade">
                <el-table 
                  v-if="filteredRecords.length > 0"
                  :data="paginatedRecords" 
                  border 
                  style="width: 100%;"
                >
                <!-- 新增：序号列 -->
                <el-table-column label="序号" width="80">
                  <template #default="scope">
                  {{ (currentPage - 1) * pageSize + scope.$index + 1 }}
                  </template>
                </el-table-column>
                
                  <el-table-column prop="id" label="ID" width="80" />
                  <el-table-column prop="doc_type" label="文书类型" width="150">
                    <template #default="scope">
                      <span style="background: #dbeafe; color: #1d4ed8; padding: 2px 8px; border-radius: 4px; font-size: 12px;">{{ scope.row.doc_type }}</span>
                    </template>
                  </el-table-column>
                  <el-table-column prop="created_at" label="生成时间" width="180" />
                  <el-table-column prop="input_fields" label="输入信息" width="250">
                    <template #default="scope">
                      <span>{{ formatFields(scope.row.input_fields) }}</span>
                    </template>
                  </el-table-column>
                  <el-table-column label="操作" width="280">
                      <template #default="scope">
                      <el-button type="primary" size="small" @click="viewRecord(scope.row)">👁️ 查看</el-button>
                      <el-button type="success" size="small" @click="exportRecord(scope.row)">📥 导出</el-button>
                      <el-button type="danger" size="small" @click="deleteRecord(scope.row.id)">🗑️ 删除</el-button>
                    </template>
                  </el-table-column>
                </el-table>
                <div v-else style="text-align: center; padding: 60px;">
                  <el-empty :description="searchKeyword || filterDocType || dateRange.length > 0 ? '没有找到符合条件的记录' : '暂无历史记录'" />
                </div>
              </transition>

              <!-- 分页 -->
              <div style="margin-top: 20px; text-align: center;" v-if="filteredRecords.length > pageSize">
                <el-pagination
                  v-model:current-page="currentPage"
                  :page-size="pageSize"
                  :total="filteredRecords.length"
                  layout="prev, pager, next"
                  background
                />
              </div>
            </el-card>
          </el-tab-pane>
        </el-tabs>

        <!-- 详情弹窗 -->
        <el-dialog 
          title="📄 文书详情" 
          v-model="showDetail" 
          width="800px"
          :close-on-click-modal="false"
        >
          <el-scrollbar style="height: 450px;">
            <pre style="white-space: pre-wrap; font-family: 'SimSun', 'Songti SC', serif; font-size: 14px; line-height: 1.8;">{{ selectedRecord?.generated_content }}</pre>
          </el-scrollbar>
          <template #footer>
            <el-button @click="showDetail = false">✖️ 关闭</el-button>
            <el-button type="success" @click="exportRecord(selectedRecord)">📥 导出文档</el-button>
          </template>
        </el-dialog>
      </el-main>
    </el-container>
  </div>
</template>

<script setup>

import { ref, onMounted, computed } from 'vue'
import axios from 'axios'
import Login from './components/Login.vue'

// 用户状态
const isLoggedIn = ref(false)
const currentUser = ref(null)

// 检查登录状态
function checkLoginStatus() {
  const token = localStorage.getItem('token')
  const user = localStorage.getItem('user')
  if (token && user) {
    isLoggedIn.value = true
    currentUser.value = JSON.parse(user)
    // 设置 axios 默认请求头
    axios.defaults.headers.common['Authorization'] = `Bearer ${token}`
  }
}

// 退出登录
function logout() {
  localStorage.removeItem('token')
  localStorage.removeItem('user')
  isLoggedIn.value = false
  currentUser.value = null
  delete axios.defaults.headers.common['Authorization']
}

// 登录成功处理
function onLoginSuccess(user) {
  isLoggedIn.value = true
  currentUser.value = user
  axios.defaults.headers.common['Authorization'] = `Bearer ${localStorage.getItem('token')}`
    loadDocTypes()
  loadHistory()
}

onMounted(() => {
  checkLoginStatus()
  if (isLoggedIn.value) {
    loadDocTypes()
    loadHistory()
  }
})

const isLoading = ref(false)

const activeTab = ref('generate')
const showDetail = ref(false)
const selectedRecord = ref(null)

const docTypes = ref([])
const selectedDocType = ref('')
const generatedContent = ref('')
const errorMsg = ref('')

const formData = ref({
  party_a: '', party_b: '', term: '', salary: '',
  plaintiff: '', defendant: '', claim: '', reason: '',
  lender: '', borrower: '', amount: '', repay_date: '',
  landlord: '', tenant: '', property: '', rent: '',
  husband: '', wife: '', child_custody: '', property_split: '',
  transferor: '', transferee: '', company_name: '', share_ratio: '',
  share_price: '', payment_method: '',
  employer: '', employee: '', confidential_scope: '', confidential_period: '',
  seller: '', buyer: '', house_address: '', house_area: '',
  house_price: '', payment_time: '',
  client: '', agent: '', matter: '', authority: '', period: ''
})

const historyRecords = ref([])

// 搜索和筛选
const searchKeyword = ref('')
const dateRange = ref([])
const filterDocType = ref('')
const currentPage = ref(1)
const pageSize = ref(10)

// 筛选后的记录
const filteredRecords = computed(() => {
  let result = historyRecords.value
  
  if (searchKeyword.value) {
    const keyword = searchKeyword.value.toLowerCase()
    result = result.filter(record => 
      record.doc_type.toLowerCase().includes(keyword) ||
      formatFields(record.input_fields).toLowerCase().includes(keyword)
    )
  }
  
  if (dateRange.value && dateRange.value.length === 2) {
    const [startDate, endDate] = dateRange.value
    result = result.filter(record => {
      const recordDate = record.created_at.split(' ')[0]
      return recordDate >= startDate && recordDate <= endDate
    })
  }
  
  if (filterDocType.value) {
    result = result.filter(record => record.doc_type === filterDocType.value)
  }
  
  return result
})

// 分页后的记录
const paginatedRecords = computed(() => {
  const start = (currentPage.value - 1) * pageSize.value
  const end = start + pageSize.value
  return filteredRecords.value.slice(start, end)
})

// 统计各类文书数量
const docTypeStats = computed(() => {
  const stats = {}
  historyRecords.value.forEach(record => {
    stats[record.doc_type] = (stats[record.doc_type] || 0) + 1
  })
  return stats
})

function filterRecords() {
  currentPage.value = 1
}

onMounted(async () => {
  await loadDocTypes()
  await loadHistory()
})

async function loadDocTypes() {
  try {
    const response = await axios.get('http://localhost:8000/api/doc-types')
    docTypes.value = response.data
    if (docTypes.value.length > 0) {
      selectedDocType.value = docTypes.value[0].name
    }
  } catch (error) {
    console.error('获取文书类型失败:', error)
    errorMsg.value = '无法连接到后端服务'
  }
}

async function generateDoc() {
  if (!selectedDocType.value) {
    errorMsg.value = '请先选择文书类型'
    return
  }
  
  isLoading.value = true
  try {
    errorMsg.value = ''
    const response = await axios.post('http://localhost:8000/api/generate', {
      doc_type: selectedDocType.value,
      fields: formData.value
    })
    if (response.data.success) {
      generatedContent.value = response.data.content
      await loadHistory()
    } else {
      errorMsg.value = '生成失败: ' + response.data.message
    }
  } catch (error) {
    console.error('生成失败:', error)
    errorMsg.value = '生成失败，请检查网络'
  } finally {
    isLoading.value = false
  }
}

function exportDoc() {
  if (!generatedContent.value) return
  downloadDoc(selectedDocType.value, generatedContent.value)
}

function exportRecord(record) {
  if (!record) return
  downloadDoc(record.doc_type, record.generated_content)
}

function downloadDoc(docType, content) {
  const blob = new Blob([content], { type: 'application/msword' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `${docType}.doc`
  a.click()
}

async function loadHistory() {
  try {
    const response = await axios.get('http://localhost:8000/api/history?limit=50')
    if (response.data.success) {
      historyRecords.value = response.data.records
    }
  } catch (error) {
    console.error('获取历史记录失败:', error)
  }
}

function viewRecord(record) {
  selectedRecord.value = record
  showDetail.value = true
}

async function deleteRecord(recordId) {
  if (!recordId) return
  
  // 确认对话框
  if (!confirm('确定要删除这条记录吗？此操作不可恢复。')) {
    return
  }
  
  try {
    const response = await axios.delete(`http://localhost:8000/api/history/${recordId}`)
    if (response.data.success) {
      // 删除成功，刷新列表
      await loadHistory()
      alert('删除成功')
    } else {
      alert('删除失败: ' + response.data.message)
    }
  } catch (error) {
    console.error('删除失败:', error)
    alert('删除失败，请检查网络')
  }
}

function formatFields(inputFields) {
  if (!inputFields) return ''
  try {
    const fields = typeof inputFields === 'string' ? JSON.parse(inputFields) : inputFields
    return Object.entries(fields)
      .filter(([_, value]) => value)
      .map(([key, value]) => `${key}: ${value}`)
      .join(', ')
  } catch {
    return inputFields
  }
}
</script>

<style scoped>
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.3s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

.slide-fade-enter-active {
  transition: all 0.3s ease-out;
}

.slide-fade-leave-active {
  transition: all 0.2s ease-in;
}

.slide-fade-enter-from {
  transform: translateX(-20px);
  opacity: 0;
}

.slide-fade-leave-to {
  transform: translateX(20px);
  opacity: 0;
}

.el-card {
  border-radius: 8px;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.08);
  transition: all 0.3s ease;
}

.el-card:hover {
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.12);
}

.el-button--primary {
  background: linear-gradient(135deg, #2563eb 0%, #1d4ed8 100%);
  border: none;
  border-radius: 6px;
}

.el-button--primary:hover {
  background: linear-gradient(135deg, #1d4ed8 0%, #1e40af 100%);
}

.el-button--success {
  background: linear-gradient(135deg, #10b981 0%, #059669 100%);
  border: none;
  border-radius: 6px;
}
</style>