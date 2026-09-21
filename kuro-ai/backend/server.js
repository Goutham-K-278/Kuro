import express from 'express';
import cors from 'cors';
import { exec } from 'child_process';
import { promisify } from 'util';
import fs from 'fs/promises';
import path from 'path';
import os from 'os';

const execAsync = promisify(exec);

const app = express();
const PORT = 3001;
const BRAIN_API = 'http://127.0.0.1:8000';  // Python brain API (main.py)

app.use(cors());
app.use(express.json({ limit: '10mb' }));

// ===== Kuro Persona System Prompts =====
const PERSONAS = {
  friendly: `You are kuro, a friendly and enthusiastic AI assistant. You're like a supportive friend who happens to be incredibly knowledgeable about technology, coding, and life. You use casual language, occasionally add emojis, and always encourage the user. You're patient, witty, and love to explain things with analogies. When the user asks for help, you go above and beyond. You call the user "buddy" or "friend" sometimes. Your name is kuro — always introduce yourself in lowercase. If they ask for a study plan, you create a detailed day-by-day plan with specific topics, resources, and mini-challenges.`,

  professional: `You are kuro, a professional AI assistant. You provide clear, well-structured answers with proper formatting. You use a respectful and knowledgeable tone. You focus on accuracy and thoroughness in your responses. Your name is kuro — always write it in lowercase.`,

  tutor: `You are kuro, a patient and brilliant coding tutor. Your name is kuro — always lowercase. Your goal is to help the user truly UNDERSTAND concepts, not just give them answers. You use the Socratic method when appropriate — asking questions to guide their thinking. When teaching, you:
  1. Explain the concept simply with an analogy
  2. Show the code with detailed comments
  3. Give a mini-challenge to test understanding
  4. Offer encouragement and track progress
  You create structured learning plans with milestones and celebrate when the user makes progress.`,

  concise: `You are kuro, a direct and efficient AI assistant. Your name is kuro — always lowercase. You give short, precise answers. You use bullet points and code snippets. Minimal fluff, maximum information.`,
};

// ===== System access capabilities =====
async function readFile(filePath) {
  try {
    const content = await fs.readFile(path.resolve(filePath), 'utf-8');
    return { success: true, content };
  } catch (err) {
    return { success: false, error: err.message };
  }
}

async function listDirectory(dirPath) {
  try {
    const items = await fs.readdir(path.resolve(dirPath), { withFileTypes: true });
    const result = items.map(item => ({
      name: item.name,
      type: item.isDirectory() ? 'directory' : 'file',
    }));
    return { success: true, items: result };
  } catch (err) {
    return { success: false, error: err.message };
  }
}

async function executeCommand(command) {
  try {
    const { stdout, stderr } = await execAsync(command, { timeout: 30000 });
    return { success: true, stdout, stderr };
  } catch (err) {
    return { success: false, error: err.message, stdout: err.stdout, stderr: err.stderr };
  }
}

// ===== Routes =====

// Health check — reports system status
app.get('/api/health', async (req, res) => {
  let brainOnline = false;
  try {
    const response = await fetch(`${BRAIN_API}/health`);
    if (response.ok) brainOnline = true;
  } catch { /* Python brain not running */ }

  res.json({
    status: 'ok',
    ollama: brainOnline,  // kept for frontend compat — actually means brain online
    model: 'kuro-brain',
    system: {
      platform: os.platform(),
      hostname: os.hostname(),
      uptime: Math.floor(os.uptime() / 60) + ' minutes',
    },
  });
});

// ===== Wake word detection (Python-powered via Porcupine) =====

// SSE proxy: forward wake events from Python backend to Electron.
// Keeps the SSE stream open PERMANENTLY — retries Python internally
// so Electron never sees a disconnect and the reconnect loop is broken.
app.get('/wake/events', (req, res) => {
  res.setHeader('Content-Type', 'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache');
  res.setHeader('Connection', 'keep-alive');
  res.setHeader('X-Accel-Buffering', 'no');

  console.log('[Backend] SSE: Client connected to wake event stream');

  let clientAlive = true;
  let retryTimer = null;
  let currentReader = null;
  let retryDelay = 2000;   // exponential backoff: starts at 2 s, caps at 15 s

  req.on('close', () => {
    clientAlive = false;
    if (retryTimer) clearTimeout(retryTimer);
    try { currentReader?.cancel(); } catch { }
    console.log('[Backend] SSE: Client disconnected');
  });

  // Send a comment-style heartbeat every 20s so the connection stays alive
  const heartbeat = setInterval(() => {
    if (!clientAlive) { clearInterval(heartbeat); return; }
    try { res.write(': ping\n\n'); } catch { clearInterval(heartbeat); }
  }, 20000);

  async function connectToPython() {
    if (!clientAlive) return;
    try {
      const pyRes = await fetch(`${BRAIN_API}/wake/events`);
      const decoder = new TextDecoder();
      currentReader = pyRes.body.getReader();
      retryDelay = 2000;  // ← reset backoff on successful connect
      console.log('[Backend] SSE: Python wake stream connected');

      while (true) {
        const { done, value } = await currentReader.read();
        if (done || !clientAlive) break;
        const chunk = decoder.decode(value, { stream: true });
        // Forward ALL data: lines from Python (wake events AND JSON alert payloads)
        if (!chunk.startsWith(':')) {
          res.write(chunk);
          if (chunk.includes('data: wake')) {
            console.log('[Backend] Wake word detected by Python!');
          } else if (chunk.includes('"type"')) {
            console.log('[Backend] SSE JSON payload forwarded');
          }
        }
      }
    } catch (err) {
      if (!clientAlive) return;
      console.log(`[Backend] SSE: Python not reachable, retrying in ${retryDelay / 1000}s...`, err.message);
    }

    // Python dropped or not yet started — retry with exponential backoff, keep Electron's SSE open
    if (clientAlive) {
      retryTimer = setTimeout(connectToPython, retryDelay);
      retryDelay = Math.min(Math.round(retryDelay * 1.5), 15000);  // cap at 15 s
    }
  }

  connectToPython();
});

// Proxy manual wake trigger directly to Python backend
app.post('/wake/trigger', async (req, res) => {
  try {
    const pyRes = await fetch(`${BRAIN_API}/wake/trigger`, { method: 'POST' });
    const data = await pyRes.json();
    res.json(data);
  } catch (err) {
    console.log('[Backend] Trigger failed:', err.message);
    res.json({ status: 'error', error: err.message });
  }
});

// Streaming dictation: record + transcribe in chunks + type into active window
app.post('/wake/dictate', async (req, res) => {
  try {
    console.log('[Backend] Starting streaming dictation...');
    const pyRes = await fetch(`${BRAIN_API}/wake/dictate`, {
      method: 'POST',
      signal: AbortSignal.timeout(180000),  // 3 min max for long dictation
    });
    const data = await pyRes.json();
    console.log(`[Backend] Dictation done: ${data.chunks || 0} chunks, ${(data.text || '').length} chars`);
    res.json(data);
  } catch (err) {
    console.log('[Backend] Dictation failed:', err.message);
    res.json({ text: '', status: 'error', error: err.message });
  }
});

// TTS proxy: tell Python brain to speak (plays via sounddevice on the local machine)
app.post('/tts/speak', async (req, res) => {
  try {
    const { text } = req.body;
    if (!text || !text.trim()) {
      return res.status(400).json({ error: 'Text is required' });
    }
    console.log(`[Backend] TTS request: "${text.slice(0, 60)}..."`);
    const pyRes = await fetch(`${BRAIN_API}/tts/speak`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
      signal: AbortSignal.timeout(15000),
    });
    if (!pyRes.ok) {
      return res.status(500).json({ error: 'TTS generation failed' });
    }
    // Python plays audio locally via sounddevice — just forward the status
    const data = await pyRes.json();
    res.json({ status: data.status || 'ok', played_server_side: true });
  } catch (err) {
    console.log('[Backend] TTS failed:', err.message);
    res.status(500).json({ error: err.message });
  }
});

// Stop TTS
app.post('/wake/stop', async (req, res) => {
  try {
    await fetch(`${BRAIN_API}/wake/stop`, { method: 'POST' });
    res.json({ status: 'ok' });
  } catch {
    res.json({ status: 'error' });
  }
});

// Check if Python brain API is online
app.get('/api/brain-health', async (req, res) => {
  try {
    const response = await fetch(`${BRAIN_API}/health`);
    const data = await response.json();
    res.json({ ...data, connected: true });
  } catch {
    res.json({ status: 'offline', brain: 'disconnected', connected: false });
  }
});

// Main chat route — proxies to Python brain API
app.post('/api/chat', async (req, res) => {
  const { message, history = [], model = 'kuro-brain', personality = 'friendly' } = req.body;

  if (!message) {
    return res.status(400).json({ error: 'Message is required' });
  }

  try {
    const brainRes = await fetch(`${BRAIN_API}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, history, model, personality }),
      signal: AbortSignal.timeout(120000),
    });
    if (brainRes.ok) {
      const data = await brainRes.json();
      console.log('[Chat] Routed via Python Brain API' + (data.is_action ? ' (ACTION)' : ''));
      return res.json({
        response: data.response || 'No response from brain',
        model: data.model || 'kuro-brain',
        via: 'brain',
        is_action: data.is_action || false,
      });
    }
    // Brain returned non-OK status
    const errText = await brainRes.text();
    return res.status(500).json({
      error: `Brain returned ${brainRes.status}`,
      response: 'Kuro brain is having issues. Please try again.',
    });
  } catch (err) {
    console.log('[Chat] Python brain offline:', err.message);
    res.status(500).json({
      error: err.message,
      response: 'Kuro brain is offline. Make sure the Python server is running on port 8000.',
    });
  }
});

// System access routes for Kuro's "hands"
app.post('/api/system/read-file', async (req, res) => {
  const { path: filePath } = req.body;
  const result = await readFile(filePath);
  res.json(result);
});

app.post('/api/system/list-dir', async (req, res) => {
  const { path: dirPath } = req.body;
  const result = await listDirectory(dirPath || '.');
  res.json(result);
});

app.post('/api/system/exec', async (req, res) => {
  const { command } = req.body;
  if (!command) return res.status(400).json({ error: 'Command is required' });
  const result = await executeCommand(command);
  res.json(result);
});

// List available models
app.get('/api/models', async (req, res) => {
  res.json({ models: [{ name: 'kuro-brain', description: 'Groq llama-3.1-8b via Python pipeline' }] });
});

// ===== Module proxy routes — forward to Python brain =====

// System info (CPU, RAM, Disk, GPU)
app.get('/api/brain/system/info', async (req, res) => {
  try {
    const r = await fetch(`${BRAIN_API}/system/info`);
    res.json(await r.json());
  } catch (err) {
    res.json({ error: 'Brain offline', details: err.message });
  }
});

// Execute a system command via Python brain
app.post('/api/brain/system/exec', async (req, res) => {
  try {
    const r = await fetch(`${BRAIN_API}/system/exec`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req.body),
    });
    res.json(await r.json());
  } catch (err) {
    res.json({ error: 'Brain offline', details: err.message });
  }
});

// Generate + run a Blender script
app.post('/api/brain/blender', async (req, res) => {
  try {
    const r = await fetch(`${BRAIN_API}/blender`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req.body),
    });
    res.json(await r.json());
  } catch (err) {
    res.json({ error: 'Brain offline', details: err.message });
  }
});

// DSA lesson
app.post('/api/brain/teach/dsa', async (req, res) => {
  try {
    const r = await fetch(`${BRAIN_API}/teach/dsa`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req.body),
    });
    res.json(await r.json());
  } catch (err) {
    res.json({ error: 'Brain offline', details: err.message });
  }
});

// RAG knowledge base query
app.post('/api/brain/rag/query', async (req, res) => {
  try {
    const r = await fetch(`${BRAIN_API}/rag/query`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req.body),
    });
    res.json(await r.json());
  } catch (err) {
    res.json({ error: 'Brain offline', details: err.message });
  }
});

// RAG ingest (re-index documents)
app.post('/api/brain/rag/ingest', async (req, res) => {
  try {
    const r = await fetch(`${BRAIN_API}/rag/ingest`, { method: 'POST' });
    res.json(await r.json());
  } catch (err) {
    res.json({ error: 'Brain offline', details: err.message });
  }
});

// ===== LLM Backend Mode Toggle (proxy → Python brain) =====

// GET /api/llm-mode — returns { mode, active, override }
app.get('/api/llm-mode', async (req, res) => {
  try {
    const r = await fetch(`${BRAIN_API}/api/llm-mode`, {
      signal: AbortSignal.timeout(3000),
    });
    res.json(await r.json());
  } catch {
    // Brain offline — return safe defaults so UI still renders
    res.json({ mode: 'auto', active: 'groq', override: false });
  }
});

// POST /api/llm-mode — body: { mode: 'auto'|'groq'|'local' }
app.post('/api/llm-mode', async (req, res) => {
  try {
    const r = await fetch(`${BRAIN_API}/api/llm-mode`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req.body),
      signal: AbortSignal.timeout(5000),
    });
    res.json(await r.json());
  } catch (err) {
    res.json({ error: 'Brain offline', details: err.message });
  }
});

// ===== Start Server =====
app.listen(PORT, () => {
  console.log(`
  ╔══════════════════════════════════════╗
  ║         Kuro Backend Server          ║
  ║        Running on port ${PORT}          ║
  ╠══════════════════════════════════════╣
  ║  API:    http://localhost:${PORT}       ║
  ║  Health: http://localhost:${PORT}/api/health ║
  ╚══════════════════════════════════════╝
  `);

  // Poll Python brain /health with exponential backoff until it becomes ready.
  // This does NOT block the server — it just logs a clear "READY" message so the
  // developer knows when the full Kuro stack is operational.
  let healthDelay = 2000;
  async function pollPythonHealth() {
    try {
      const res = await fetch(`${BRAIN_API}/health`, { signal: AbortSignal.timeout(3000) });
      if (res.ok) {
        const data = await res.json();
        if (data.ready) {
          console.log('[Backend] ✅ Python Brain is READY — full Kuro stack operational!');
          return;  // stop polling
        }
        console.log('[Backend] Python Brain started but still loading... retrying');
      }
    } catch {
      console.log(`[Backend] Waiting for Python Brain (retry in ${healthDelay / 1000}s)...`);
    }
    // Not ready yet — retry with backoff
    setTimeout(pollPythonHealth, healthDelay);
    healthDelay = Math.min(Math.round(healthDelay * 1.5), 15000);
  }
  // Start polling after a short initial delay (give Python a head start)
  setTimeout(pollPythonHealth, 2000);
});