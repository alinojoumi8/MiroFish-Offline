import axios from 'axios'

// Create axios instance
const service = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '',
  timeout: 300000, // 5 minute timeout (ontology generation may require longer time)
  headers: {
    'Content-Type': 'application/json'
  }
})

export const extractApiErrorMessage = (payload, fallback = 'Error') => {
  if (payload?.error && typeof payload.error === 'object') {
    return payload.error.message || fallback
  }
  return payload?.error || payload?.message || fallback
}

// Request interceptor
service.interceptors.request.use(
  config => {
    return config
  },
  error => {
    console.error('Request error:', error)
    return Promise.reject(error)
  }
)

// Response interceptor (fault-tolerant retry mechanism)
service.interceptors.response.use(
  response => {
    const res = response.data

    // If the returned status code is not success, throw error
    if (!res.success && res.success !== undefined) {
      const message = extractApiErrorMessage(res)
      console.error('API Error:', message)
      return Promise.reject(new Error(message))
    }

    return res
  },
  error => {
    const apiPayload = error.response?.data
    const apiMessage = extractApiErrorMessage(apiPayload, error.message)
    console.error('Response error:', apiMessage)

    // Handle timeout
    if (error.code === 'ECONNABORTED' && error.message.includes('timeout')) {
      console.error('Request timeout')
    }

    // Handle network error
    if (error.message === 'Network Error') {
      console.error('Network error - please check your connection')
    }

    if (apiPayload?.error) {
      return Promise.reject(new Error(apiMessage))
    }
    return Promise.reject(error)
  }
)

// Request function with retry
export const requestWithRetry = async (requestFn, maxRetries = 3, delay = 1000) => {
  for (let i = 0; i < maxRetries; i++) {
    try {
      return await requestFn()
    } catch (error) {
      if (i === maxRetries - 1) throw error

      console.warn(`Request failed, retrying (${i + 1}/${maxRetries})...`)
      await new Promise(resolve => setTimeout(resolve, delay * Math.pow(2, i)))
    }
  }
}

export default service
