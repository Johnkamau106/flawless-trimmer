import axios from 'axios'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:5000'

export const api = axios.create({
  baseURL: API_BASE_URL,
  timeout: 300000,
})

export async function inspectUrl(url) {
  const { data } = await api.post('/api/inspect', { url })
  return data
}

export async function downloadMedia(payload, onProgress) {
  const source = await api.post('/api/download', payload, {
    responseType: 'blob',
    onDownloadProgress: onProgress,
  })
  return source
}

export async function saveClip(payload) {
  const { data } = await api.post('/api/clip', payload)
  return data
}

export async function listClips() {
  const { data } = await api.get('/api/clips')
  return data
}


