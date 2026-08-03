import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  withCredentials: true,
  timeout: 120000,
  headers: { 'Content-Type': 'application/json' },
})

export default api
