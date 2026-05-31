<template>
  <div id="app">
    <el-container style="height: 100vh;">
      <!-- 头部 -->
      <el-header style="background-color: #409EFF; color: white; line-height: 60px;">
        <h1 style="margin: 0; color: white;">智能法律文书辅助生成系统</h1>
      </el-header>

      <el-main style="background-color: #f5f5f5;">
        <el-alert v-if="errorMsg" :title="errorMsg" type="error" style="margin-bottom: 20px;" />

        <el-tabs v-model="activeTab" type="card">
          <!-- 生成页面 -->
          <el-tab-pane label="文书生成" name="generate">
            <el-row :gutter="20">
              <el-col :span="8">
                <el-card style="margin-bottom: 20px;">
                  <template #header>
                    <span>选择文书类型</span>
                  </template>
                  <el-radio-group v-model="selectedDocType">
                    <el-radio v-for="doc in docTypes" :key="doc.id" :value="doc.name" style="display: block; margin-bottom: 10px;">
                      {{ doc.name }}
                    </el-radio>
                  </el-radio-group>
                </el-card>

                <el-card>
                  <template #header>
                    <span>填写信息</span>
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
                  </el-form>

                  <el-button type="primary" @click="generateDoc" style="width: 100%; margin-top: 20px;">
                    生成文书
                  </el-button>
                </el-card>
              </el-col>

              <el-col :span="16">
                <el-card>
                  <template #header>
                    <span>生成结果</span>
                  </template>
                  <el-scrollbar style="height: 400px; background-color: white; padding: 10px;">
                    <pre style="margin: 0; white-space: pre-wrap;">{{ generatedContent || '请选择文书类型并填写信息，然后点击"生成文书"' }}</pre>
                  </el-scrollbar>
                  <el-button type="success" @click="exportDoc" style="margin-top: 20px;" :disabled="!generatedContent">
                    导出文档
                  </el-button>
                </el-card>
              </el-col>
            </el-row>
          </el-tab-pane>

          <!-- 历史记录页面 -->
          <el-tab-pane label="历史记录" name="history">
            <el-card>
              <template #header>
                <span>生成历史记录</span>
                <el-button type="primary" size="small" @click="loadHistory" style="float: right;">
                  刷新记录
                </el-button>
              </template>

              <el-table :data="historyRecords" border style="width: 100%;">
                <el-table-column prop="id" label="ID" width="80" />
                <el-table-column prop="doc_type" label="文书类型" width="150" />
                <el-table-column prop="created_at" label="生成时间" width="180" />
                <el-table-column prop="input_fields" label="输入信息" width="250">
                  <template #default="scope">
                    <span>{{ formatFields(scope.row.input_fields) }}</span>
                  </template>
                </el-table-column>
                <el-table-column label="操作" width="200">
                  <template #default="scope">
                    <el-button type="text" @click="viewRecord(scope.row)">查看详情</el-button>
                    <el-button type="text" @click="exportRecord(scope.row)">导出</el-button>
                  </template>
                </el-table-column>
              </el-table>

              <div v-if="historyRecords.length === 0" style="text-align: center; padding: 40px;">
                <el-empty description="暂无历史记录" />
              </div>
            </el-card>
          </el-tab-pane>
        </el-tabs>

        <!-- 详情弹窗 -->
        <el-dialog title="文书详情" :visible.sync="showDetail" width="800px">
          <el-scrollbar style="height: 400px;">
            <pre style="white-space: pre-wrap;">{{ selectedRecord?.generated_content }}</pre>
          </el-scrollbar>
          <template #footer>
            <el-button @click="showDetail = false">关闭</el-button>
            <el-button type="success" @click="exportRecord(selectedRecord)">导出文档</el-button>
          </template>
        </el-dialog>
      </el-main>
    </el-container>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import axios from 'axios'

// 页面状态
const activeTab = ref('generate')
const showDetail = ref(false)
const selectedRecord = ref(null)

// 文书类型和表单数据
const docTypes = ref([])
const selectedDocType = ref('')
const generatedContent = ref('')
const errorMsg = ref('')

const formData = ref({
  party_a: '', party_b: '', term: '', salary: '',
  plaintiff: '', defendant: '', claim: '', reason: '',
  lender: '', borrower: '', amount: '', repay_date: '',
  landlord: '', tenant: '', property: '', rent: ''
})

// 历史记录
const historyRecords = ref([])

// 获取文书类型
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

// 生成文书
async function generateDoc() {
  if (!selectedDocType.value) {
    errorMsg.value = '请先选择文书类型'
    return
  }
  try {
    errorMsg.value = ''
    const response = await axios.post('http://localhost:8000/api/generate', {
      doc_type: selectedDocType.value,
      fields: formData.value
    })
    if (response.data.success) {
      generatedContent.value = response.data.content
      await loadHistory() // 生成后刷新历史记录
    } else {
      errorMsg.value = '生成失败: ' + response.data.message
    }
  } catch (error) {
    console.error('生成失败:', error)
    errorMsg.value = '生成失败，请检查网络'
  }
}

// 导出当前文档
function exportDoc() {
  if (!generatedContent.value) return
  downloadDoc(selectedDocType.value, generatedContent.value)
}

// 导出记录文档
function exportRecord(record) {
  if (!record) return
  downloadDoc(record.doc_type, record.generated_content)
}

// 通用下载函数
function downloadDoc(docType, content) {
  const blob = new Blob([content], { type: 'application/msword' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `${docType}.doc`
  a.click()
}

// 加载历史记录
async function loadHistory() {
  try {
    const response = await axios.get('http://localhost:8000/api/history')
    if (response.data.success) {
      historyRecords.value = response.data.records
    }
  } catch (error) {
    console.error('获取历史记录失败:', error)
  }
}

// 查看记录详情
function viewRecord(record) {
  selectedRecord.value = record
  showDetail.value = true
}

// 格式化输入字段
function formatFields(fields) {
  if (!fields) return ''
  // 如果是字符串，尝试解析为对象
  const data = typeof fields === 'string' ? JSON.parse(fields) : fields
  const arr = []
  for (const key in data) {
    if (data[key]) {
      arr.push(`${data[key]}`)
    }
  }
  return arr.join(' / ') || '无'
}
</script>

<style>
#app { 
  font-family: 'Microsoft YaHei', sans-serif; 
}
</style>