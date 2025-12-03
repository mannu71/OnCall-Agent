import { app, BrowserWindow, ipcMain } from "electron";
import path from "path";
import fs from "fs";
import { fileURLToPath } from "url";
import { exec, spawn } from "child_process";
import { promisify } from "util";

const execAsync = promisify(exec);

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Import file utilities
import { saveSchedulesToFile, loadSchedulesFromFile, saveWorkflowsToFile, loadWorkflowsFromFile, saveMCPConfigToFile, loadMCPConfigFromFile, saveLLMConfigToFile, loadLLMConfigFromFile, setLLMApiKey, getLLMApiKeyMasked, getLLMApiKey, hasLLMApiKey, deleteLLMApiKey, triggerWorkflow, runAgentWorkflow, saveSqlFile, loadSqlFile, loadWorkflowRuns, loadLatestWorkflowResult, loadWorkflowResult, clearWorkflowOutputs, getPathsInfo } from './fileUtils.js';

// IPC handlers for schedule operations
ipcMain.handle('schedules:load', async () => {
  try {
    const schedules = loadSchedulesFromFile();
    return schedules;
  } catch (error) {
    console.error('Error loading schedules:', error);
    return [];
  }
});

ipcMain.handle('schedules:save', async (event, schedules) => {
  try {
    const result = saveSchedulesToFile(schedules);
    return result;
  } catch (error) {
    console.error('Error saving schedules:', error);
    return { success: false, error: error.message };
  }
});

// IPC handlers for multiple workflows
ipcMain.handle('workflows:load', async () => {
  try {
    const workflows = loadWorkflowsFromFile();
    return workflows;
  } catch (error) {
    console.error('Error loading workflows:', error);
    return [];
  }
});

ipcMain.handle('workflows:save', async (event, workflows) => {
  try {
    console.log('Electron main: workflows:save IPC called with data:', workflows);
    const result = saveWorkflowsToFile(workflows);
    console.log('Electron main: saveWorkflowsToFile result:', result);
    return result;
  } catch (error) {
    console.error('Error saving workflows:', error);
    return { success: false, error: error.message };
  }
});

// IPC to trigger a specific workflow
ipcMain.handle('workflows:trigger', async (event, workflowName) => {
  try {
    const result = triggerWorkflow(workflowName);
    return result;
  } catch (error) {
    console.error('Error triggering workflow:', error);
    return { success: false, error: error.message };
  }
});

// IPC to run an agent with a user query
ipcMain.handle('agent:run', async (event, workflowName, userQuery) => {
  try {
    // Get the sender window to send progress events
    const senderWindow = BrowserWindow.fromWebContents(event.sender);
    
    // Progress callback that sends events to the renderer
    const progressCallback = (progress) => {
      if (senderWindow && !senderWindow.isDestroyed()) {
        senderWindow.webContents.send('agent:progress', progress);
      }
    };
    
    const result = await runAgentWorkflow(workflowName, userQuery, progressCallback);
    return result;
  } catch (error) {
    console.error('Error running agent:', error);
    return { success: false, error: error.message };
  }
});

// IPC handlers for MCP server configuration
ipcMain.handle('mcp-config:load', async () => {
  try {
    const config = loadMCPConfigFromFile();
    return config;
  } catch (error) {
    console.error('Error loading MCP config:', error);
    return { servers: {}, inputs: [] };
  }
});

// IPC handler for testing MCP server connection
ipcMain.handle('mcp-server:test', async (event, serverName, serverConfig) => {
  try {
    console.log(`Testing connection to MCP server: ${serverName}`);
    
    const { command, args = [], env = {} } = serverConfig;
    
    if (!command) {
      return { success: false, error: 'No command specified' };
    }
    
    // Create a promise that spawns the process and checks if it starts successfully
    return new Promise((resolve) => {
      const processEnv = { ...process.env, ...env };
      const child = spawn(command, args, { 
        env: processEnv,
        stdio: ['pipe', 'pipe', 'pipe'],
        shell: true
      });
      
      let stdout = '';
      let stderr = '';
      let resolved = false;
      
      // Set a timeout for the connection test (5 seconds to allow for slower connections)
      const timeout = setTimeout(() => {
        if (!resolved) {
          resolved = true;
          child.kill();
          // If process was running for 5 seconds without error, consider it successful
          resolve({ 
            success: true, 
            message: `Connected`,
            output: stdout || 'Connection established'
          });
        }
      }, 5000);
      
      child.stdout.on('data', (data) => {
        stdout += data.toString();
        console.log(`[${serverName}] stdout:`, data.toString().substring(0, 200));
      });
      
      child.stderr.on('data', (data) => {
        stderr += data.toString();
        console.log(`[${serverName}] stderr:`, data.toString().substring(0, 200));
        
        // Check for common error patterns
        const errStr = stderr.toLowerCase();
        if (errStr.includes('enoent') || errStr.includes('not found') || errStr.includes('cannot find')) {
          if (!resolved) {
            resolved = true;
            clearTimeout(timeout);
            child.kill();
            resolve({ success: false, error: `Command not found: ${command}` });
          }
        } else if (errStr.includes('econnrefused') || errStr.includes('connection refused')) {
          if (!resolved) {
            resolved = true;
            clearTimeout(timeout);
            child.kill();
            resolve({ success: false, error: 'Connection refused - check host/port' });
          }
        } else if (errStr.includes('authentication') || errStr.includes('password') || errStr.includes('denied')) {
          if (!resolved) {
            resolved = true;
            clearTimeout(timeout);
            child.kill();
            resolve({ success: false, error: 'Authentication failed' });
          }
        } else if (errStr.includes('certificate') || errStr.includes('ssl') || errStr.includes('tls')) {
          if (!resolved) {
            resolved = true;
            clearTimeout(timeout);
            child.kill();
            resolve({ success: false, error: 'SSL/Certificate error - check NODE_EXTRA_CA_CERTS' });
          }
        } else if (errStr.includes('timeout') || errStr.includes('timed out')) {
          if (!resolved) {
            resolved = true;
            clearTimeout(timeout);
            child.kill();
            resolve({ success: false, error: 'Connection timeout' });
          }
        }
      });
      
      child.on('error', (err) => {
        console.log(`[${serverName}] error:`, err.message);
        if (!resolved) {
          resolved = true;
          clearTimeout(timeout);
          resolve({ success: false, error: err.message });
        }
      });
      
      child.on('exit', (code) => {
        console.log(`[${serverName}] exit code:`, code, 'stderr:', stderr.substring(0, 300));
        if (!resolved) {
          resolved = true;
          clearTimeout(timeout);
          if (code === 0 || code === null) {
            resolve({ 
              success: true, 
              message: `Connected`,
              output: stdout
            });
          } else {
            // Try to extract a meaningful error from stderr
            let errorMsg = `Exit code ${code}`;
            if (stderr) {
              // Get the last meaningful line from stderr
              const lines = stderr.trim().split('\n').filter(l => l.trim());
              if (lines.length > 0) {
                errorMsg = lines[lines.length - 1].substring(0, 50);
              }
            }
            resolve({ 
              success: false, 
              error: errorMsg
            });
          }
        }
      });
    });
  } catch (error) {
    console.error('Error testing MCP server:', error);
    return { success: false, error: error.message };
  }
});

ipcMain.handle('mcp-config:save', async (event, config) => {
  try {
    const result = saveMCPConfigToFile(config);
    return result;
  } catch (error) {
    console.error('Error saving MCP config:', error);
    return { success: false, error: error.message };
  }
});

// IPC handlers for LLM configuration
ipcMain.handle('llm-config:load', async () => {
  try {
    const config = loadLLMConfigFromFile();
    return config;
  } catch (error) {
    console.error('Error loading LLM config:', error);
    return { llms: {} };
  }
});

ipcMain.handle('llm-config:save', async (event, config) => {
  try {
    const result = saveLLMConfigToFile(config);
    return result;
  } catch (error) {
    console.error('Error saving LLM config:', error);
    return { success: false, error: error.message };
  }
});

// IPC handlers for LLM API key management (stored in LLM config)
ipcMain.handle('llm-config:set-api-key', async (event, llmName, apiKey) => {
  try {
    const result = setLLMApiKey(llmName, apiKey);
    return result;
  } catch (error) {
    console.error('Error setting API key:', error);
    return { success: false, error: error.message };
  }
});

ipcMain.handle('llm-config:get-api-key-masked', async (event, llmName) => {
  try {
    const masked = getLLMApiKeyMasked(llmName);
    return { success: true, masked };
  } catch (error) {
    console.error('Error getting masked API key:', error);
    return { success: false, error: error.message };
  }
});

ipcMain.handle('llm-config:has-api-key', async (event, llmName) => {
  try {
    const exists = hasLLMApiKey(llmName);
    return { success: true, exists };
  } catch (error) {
    console.error('Error checking API key:', error);
    return { success: false, error: error.message };
  }
});

ipcMain.handle('llm-config:delete-api-key', async (event, llmName) => {
  try {
    const result = deleteLLMApiKey(llmName);
    return result;
  } catch (error) {
    console.error('Error deleting API key:', error);
    return { success: false, error: error.message };
  }
});

// IPC handler for testing LLM connection
ipcMain.handle('llm:test', async (event, llmName, llmConfig) => {
  try {
    console.log(`Testing LLM connection: ${llmName}`);
    
    const { provider, model, endpoint, baseUrl } = llmConfig;
    const apiKey = getLLMApiKey(llmName);
    
    // Ollama doesn't require an API key
    if (provider !== 'Ollama' && !apiKey) {
      return { success: false, error: 'No API key configured' };
    }
    
    let testUrl;
    let headers = { 'Content-Type': 'application/json' };
    let body;
    
    switch (provider) {
      case 'OpenAI':
        testUrl = 'https://api.openai.com/v1/models';
        headers['Authorization'] = `Bearer ${apiKey}`;
        break;
        
      case 'Anthropic':
        // Use a HEAD-like request that validates API key without burning tokens
        // We make a request with invalid content to trigger auth check before usage
        testUrl = 'https://api.anthropic.com/v1/messages';
        headers['x-api-key'] = apiKey;
        headers['anthropic-version'] = '2023-06-01';
        // Send empty messages to validate API key - will return 400 if key valid, 401 if invalid
        body = JSON.stringify({
          model: model || 'claude-3-5-sonnet-20241022',
          max_tokens: 1,
          messages: []
        });
        break;
        
      case 'Groq':
        testUrl = 'https://api.groq.com/openai/v1/models';
        headers['Authorization'] = `Bearer ${apiKey}`;
        break;
        
      case 'Google':
        testUrl = `https://generativelanguage.googleapis.com/v1beta/models?key=${apiKey}`;
        break;
        
      case 'Azure OpenAI':
        if (!endpoint) {
          return { success: false, error: 'Azure endpoint URL is required' };
        }
        testUrl = `${endpoint}/openai/models?api-version=2024-02-01`;
        headers['api-key'] = apiKey;
        break;
        
      case 'Ollama':
        const ollamaUrl = baseUrl || 'http://localhost:11434';
        testUrl = `${ollamaUrl}/api/tags`;
        break;
        
      default:
        return { success: false, error: `Unknown provider: ${provider}` };
    }
    
    // Use built-in fetch (Node.js 18+)
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 10000);
    
    const fetchOptions = {
      method: body ? 'POST' : 'GET',
      headers,
      signal: controller.signal
    };
    
    if (body) {
      fetchOptions.body = body;
    }
    
    try {
      const response = await fetch(testUrl, fetchOptions);
      clearTimeout(timeoutId);
      
      if (response.ok) {
        return { success: true, message: `Connected to ${provider}` };
      } else {
        const errorText = await response.text();
        let errorMsg = `HTTP ${response.status}`;
        try {
          const errorJson = JSON.parse(errorText);
          errorMsg = errorJson.error?.message || errorJson.message || errorMsg;
        } catch {
          errorMsg = errorText.substring(0, 100) || errorMsg;
        }
        
        // For Anthropic, a 400 error with valid API key means connection works
        // (we send empty messages intentionally to avoid burning tokens)
        if (provider === 'Anthropic' && response.status === 400) {
          return { success: true, message: `Connected to ${provider}` };
        }
        
        return { success: false, error: errorMsg };
      }
    } finally {
      clearTimeout(timeoutId);
    }
  } catch (error) {
    console.error('Error testing LLM:', error);
    if (error.name === 'AbortError') {
      return { success: false, error: 'Connection timeout' };
    }
    if (error.code === 'ECONNREFUSED') {
      return { success: false, error: 'Connection refused - check if service is running' };
    }
    if (error.code === 'ENOTFOUND') {
      return { success: false, error: 'Host not found - check endpoint URL' };
    }
    return { success: false, error: error.message };
  }
});

// IPC to save SQL file
ipcMain.handle('sql:save', async (event, filename, content) => {
  try {
    const result = saveSqlFile(filename, content);
    return result;
  } catch (error) {
    console.error('Error saving SQL file:', error);
    return { success: false, error: error.message };
  }
});

// IPC to load SQL file
ipcMain.handle('sql:load', async (event, relativePath) => {
  try {
    const result = loadSqlFile(relativePath);
    return result;
  } catch (error) {
    console.error('Error loading SQL file:', error);
    return { success: false, error: error.message };
  }
});

// IPC to load workflow execution runs
ipcMain.handle('workflow-runs:load', async () => {
  try {
    const runs = loadWorkflowRuns();
    return runs;
  } catch (error) {
    console.error('Error loading workflow runs:', error);
    return [];
  }
});

// IPC to load latest workflow result details
ipcMain.handle('workflow-result:latest', async () => {
  try {
    const result = loadLatestWorkflowResult();
    return result;
  } catch (error) {
    console.error('Error loading latest workflow result:', error);
    return null;
  }
});

// IPC to load specific workflow result by filename
ipcMain.handle('workflow-result:load', async (event, filename) => {
  try {
    const result = loadWorkflowResult(filename);
    return result;
  } catch (error) {
    console.error('Error loading workflow result:', error);
    return null;
  }
});

// IPC to clear all workflow output files
ipcMain.handle('workflow-outputs:clear', async () => {
  try {
    const result = clearWorkflowOutputs();
    return result;
  } catch (error) {
    console.error('Error clearing workflow outputs:', error);
    return { success: false, error: error.message };
  }
});

// IPC to get paths debug info
ipcMain.handle('debug:paths', async () => {
  try {
    return getPathsInfo();
  } catch (error) {
    console.error('Error getting paths info:', error);
    return { error: error.message };
  }
});

// Docker management functions
// Get path to bundled docker-compose.yml (in resources)
function getBundledDockerComposePath() {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, 'agent', 'docker-compose.yml');
  }
  return path.join(__dirname, '..', '..', 'agent', 'docker-compose.yml');
}

// Get writable agent path (for data, triggers, output)
function getWritableAgentPath() {
  if (app.isPackaged) {
    return path.join(app.getPath('userData'), 'agent');
  }
  return path.join(__dirname, '..', '..', 'agent');
}

// Create a docker-compose.yml that uses the writable paths for packaged app
function createDockerComposeForPackaged() {
  if (!app.isPackaged) {
    return getBundledDockerComposePath();
  }
  
  const writableAgentDir = getWritableAgentPath();
  const bundledAgentDir = path.join(process.resourcesPath, 'agent');
  
  // Create a modified docker-compose.yml in the writable directory
  const composeContent = `services:
  agent:
    build: "${bundledAgentDir.replace(/\\/g, '/')}"
    container_name: oncall-agent
    restart: unless-stopped
    environment:
      - NODE_ENV=production
      - LOG_LEVEL=info
      - WORKFLOWS_FILE=/app/data/config/workflows.json
      - TRIGGER_DIR=/app/data/config/triggers
      - OUTPUT_DIR=/app/output
      - SCHEDULER_FILE=/app/data/config/schedules.json
    volumes:
      # Mount writable data directory for config (schedules, workflows, triggers, sql)
      - "${writableAgentDir.replace(/\\/g, '/')}/data:/app/data"
      
      # Mount writable output directory
      - "${writableAgentDir.replace(/\\/g, '/')}/output:/app/output"
      
      # Mount writable logs directory
      - "${writableAgentDir.replace(/\\/g, '/')}/logs:/app/logs"
    networks:
      - oncall-network

networks:
  oncall-network:
    driver: bridge
`;
  
  const writableComposePath = path.join(writableAgentDir, 'docker-compose.yml');
  
  // Ensure directory exists
  if (!fs.existsSync(writableAgentDir)) {
    fs.mkdirSync(writableAgentDir, { recursive: true });
  }
  
  fs.writeFileSync(writableComposePath, composeContent);
  console.log('Created docker-compose.yml at:', writableComposePath);
  
  return writableComposePath;
}

function getDockerComposePath() {
  return createDockerComposeForPackaged();
}

// IPC: Check if Docker is available
ipcMain.handle('docker:check', async () => {
  try {
    await execAsync('docker --version');
    await execAsync('docker-compose --version');
    return { available: true, error: null };
  } catch (error) {
    return { available: false, error: 'Docker Desktop is not installed or not running' };
  }
});

// IPC: Start Docker services
ipcMain.handle('docker:start', async () => {
  try {
    const composePath = getDockerComposePath();
    const agentDir = path.dirname(composePath);
    
    const { stdout, stderr } = await execAsync(
      `docker-compose -f "${composePath}" up -d --build`,
      { cwd: agentDir }
    );
    
    return { success: true, message: 'Docker services started successfully', output: stdout };
  } catch (error) {
    console.error('Docker start error:', error);
    return { success: false, error: error.message };
  }
});

// IPC: Stop Docker services
ipcMain.handle('docker:stop', async () => {
  try {
    const composePath = getDockerComposePath();
    const agentDir = path.dirname(composePath);
    
    const { stdout, stderr } = await execAsync(
      `docker-compose -f "${composePath}" down`,
      { cwd: agentDir }
    );
    
    return { success: true, message: 'Docker services stopped successfully', output: stdout };
  } catch (error) {
    console.error('Docker stop error:', error);
    return { success: false, error: error.message };
  }
});

// IPC: Get Docker container status
ipcMain.handle('docker:status', async () => {
  try {
    const composePath = getDockerComposePath();
    const agentDir = path.dirname(composePath);
    
    const { stdout } = await execAsync(
      `docker-compose -f "${composePath}" ps --format json`,
      { cwd: agentDir }
    );
    
    // Parse JSON output
    const containers = stdout.trim().split('\n')
      .filter(line => line.trim())
      .map(line => {
        try {
          return JSON.parse(line);
        } catch (e) {
          return null;
        }
      })
      .filter(Boolean);
    
    return { success: true, containers };
  } catch (error) {
    console.error('Docker status error:', error);
    return { success: false, containers: [], error: error.message };
  }
});

function createWindow() {
  const win = new BrowserWindow({
    title: "OnCall Agent",
    width: 1200,
    height: 800,
    icon: path.join(__dirname, "..", "public", "favicon.ico"),
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      nodeIntegration: false,
      contextIsolation: true
    }
  });

  // Load from built files in production, localhost in development
  if (app.isPackaged) {
    // In packaged app, load from asar
    win.loadFile(path.join(__dirname, "..", "dist", "index.html"));
  } else {
    win.loadURL("http://localhost:5173");
  }
}

app.whenReady().then(() => {
  createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});