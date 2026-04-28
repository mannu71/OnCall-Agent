import { app, BrowserWindow } from "electron";
import path from "node:path";
import fs from "node:fs";
import { fileURLToPath } from "node:url";
import { spawn } from "node:child_process";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// FastAPI server process reference
let fastApiProcess = null;
const FASTAPI_PORT = 8000;

// Get the path to the FastAPI backend
const getFastApiPath = () => {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, 'agent-api');
  }
  return path.join(__dirname, '..', '..', 'agent-api');
};

// Get the path to Python executable (for packaged app, use embedded Python or system Python)
const getPythonPath = () => {
  if (app.isPackaged) {
    // In packaged app, check for embedded Python or use system Python
    const embeddedPython = path.join(process.resourcesPath, 'python', 'python.exe');
    if (fs.existsSync(embeddedPython)) {
      return embeddedPython;
    }
    // Fall back to system Python
    return 'python';
  }
  // In development, use system Python
  return 'python';
};

// Start the FastAPI server
async function startFastApiServer() {
  if (fastApiProcess) {
    console.log('FastAPI server already running');
    return true;
  }

  const fastApiPath = getFastApiPath();
  const pythonPath = getPythonPath();

  console.log('Starting FastAPI server...');
  console.log('FastAPI path:', fastApiPath);
  console.log('Python path:', pythonPath);

  // Check if FastAPI directory exists
  if (!fs.existsSync(fastApiPath)) {
    console.error('FastAPI directory not found:', fastApiPath);
    return false;
  }

  return new Promise((resolve) => {
    try {
      // Start uvicorn server
      fastApiProcess = spawn(pythonPath, [
        '-m', 'uvicorn', 'app.main:app',
        '--host', '127.0.0.1',
        '--port', String(FASTAPI_PORT),
        '--log-level', 'info'
      ], {
        cwd: fastApiPath,
        stdio: ['pipe', 'pipe', 'pipe'],
        shell: true
      });

      fastApiProcess.stdout.on('data', (data) => {
        console.log(`[FastAPI] ${data.toString().trim()}`);
      });

      fastApiProcess.stderr.on('data', (data) => {
        console.error(`[FastAPI Error] ${data.toString().trim()}`);
      });

      fastApiProcess.on('error', (err) => {
        console.error('Failed to start FastAPI server:', err);
        fastApiProcess = null;
        resolve(false);
      });

      fastApiProcess.on('exit', (code) => {
        console.log(`FastAPI server exited with code ${code}`);
        fastApiProcess = null;
      });

      // Wait a bit for server to start, then check health
      setTimeout(async () => {
        try {
          const response = await fetch(`http://127.0.0.1:${FASTAPI_PORT}/api/v1/health`);
          if (response.ok) {
            console.log('FastAPI server started successfully');
            resolve(true);
          } else {
            console.error('FastAPI server health check failed');
            resolve(false);
          }
        } catch (err) {
          console.error('FastAPI server health check error:', err.message);
          // Server might still be starting, resolve true anyway
          resolve(true);
        }
      }, 3000);

    } catch (err) {
      console.error('Error spawning FastAPI process:', err);
      resolve(false);
    }
  });
}

// Stop the FastAPI server
function stopFastApiServer() {
  if (fastApiProcess) {
    console.log('Stopping FastAPI server...');
    fastApiProcess.kill();
    fastApiProcess = null;
  }
}

// Check if FastAPI server is running
async function isFastApiRunning() {
  try {
    const response = await fetch(`http://127.0.0.1:${FASTAPI_PORT}/api/v1/health`, {
      method: 'GET',
      signal: AbortSignal.timeout(2000)
    });
    return response.ok;
  } catch {
    return false;
  }
}

// ============================================
// Window Management
// ============================================

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
    const devUrl = "http://localhost:5173";
    console.log(`Loading dev URL: ${devUrl}`);
    win.loadURL(devUrl).catch(err => {
      console.error('Failed to load URL:', err);
      console.error('Make sure Vite dev server is running on port 5173');
    });
  }

  // Open DevTools in development
  if (!app.isPackaged) {
    win.webContents.openDevTools();
  }

  // Log when window is ready
  win.webContents.on('did-finish-load', () => {
    console.log('Window loaded successfully');
  });

  win.webContents.on('did-fail-load', (event, errorCode, errorDescription) => {
    console.error('Failed to load window:', errorCode, errorDescription);
  });
}

// ============================================
// App Lifecycle
// ============================================

app.whenReady().then(async () => {
  console.log('Electron app is ready');
  console.log('App is packaged:', app.isPackaged);

  // Start FastAPI server when packaged
  if (app.isPackaged) {
    console.log('Starting FastAPI backend server...');
    const serverStarted = await startFastApiServer();
    if (!serverStarted) {
      console.error('Failed to start FastAPI server, some features may not work');
    }
  } else {
    // In development, check if FastAPI server is already running
    console.log('Development mode - checking if FastAPI server is running...');
    const isRunning = await isFastApiRunning();
    if (isRunning) {
      console.log('FastAPI server is already running');
    } else {
      console.log('FastAPI server is not running. Start it manually with: cd agent-api && python -m uvicorn app.main:app --reload');
    }
  }

  createWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
}).catch(err => {
  console.error('Error during app initialization:', err);
  process.exit(1);
});

app.on("window-all-closed", () => {
  // Stop FastAPI server when app closes
  stopFastApiServer();

  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", () => {
  // Ensure FastAPI server is stopped before quit
  stopFastApiServer();
});
