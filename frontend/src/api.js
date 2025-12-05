import axios from 'axios'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api'

// Axios instance
const api = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
})

export const transcriptionAPI = {
  // Preferred: Direct upload to S3, then trigger SageMaker transcription
  upload: async (file, onProgress = null) => {
    try {
      // 1. Ask backend for presigned POST
      const presignRes = await api.post('/s3/presign', {
        filename: file.name
      })
      const { presign, key, bucket } = presignRes.data

      // 2. Upload to S3 directly using the presigned POST
      const formData = new FormData()
      Object.entries(presign.fields).forEach(([k, v]) => {
        formData.append(k, v)
      })
      formData.append('file', file)

      // Use fetch (NOT axios) to avoid content-type override issues
      const uploadResp = await fetch(presign.url, {
        method: 'POST',
        body: formData,
      })

      if (!uploadResp.ok) {
        throw new Error(`S3 upload failed: ${uploadResp.statusText}`)
      }

      // 3. Tell backend to run SageMaker processing using this S3 object
      const triggerRes = await api.post('/s3/trigger', {
        s3_key: key
      })

      return triggerRes.data

    } catch (err) {
      console.error('Direct S3 upload failed. Falling back to /upload.', err)

      // ---- FALLBACK: old /upload route ----
      const fallbackFormData = new FormData()
      fallbackFormData.append('file', file)

      const fallbackResp = await api.post('/upload', fallbackFormData, {
        headers: { 'Content-Type': 'multipart/form-data' }
      })
      return fallbackResp.data
    }
  },

  saveDirectTranscript: async (filename, transcript, speakers = '[]') => {
    const response = await api.post('/transcript', {
      filename,
      transcript,
      speakers
    })
    return response.data
  },

  get: async (id) => {
    const response = await api.get(`/transcription/${id}`)
    return response.data
  },

  summarize: async (id, language = 'en', temperature = 0.8) => {
    const response = await api.post(`/summarize/${id}`, null, {
      params: { language, temperature }
    })
    return response.data
  },

  export: async (id) => {
    const response = await api.get(`/export/${id}`, {
      responseType: 'blob',
    })
    return response.data
  },
}

export const appAPI = {
  getStatus: async () => {
    const response = await api.get('/')
    return response.data
  },
}

export default api
