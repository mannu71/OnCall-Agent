import React, { useState, useEffect, useRef } from 'react';
import { getLLMs } from '../../services/llmService';

const NodeConfigPanel = ({ node, onUpdate, onClose }) => {
  const [config, setConfig] = useState(node?.data || {});
  const [availableLLMs, setAvailableLLMs] = useState({});
  const [isLoadingLLMs, setIsLoadingLLMs] = useState(true);
  const fileInputRef = useRef(null);

  // Load configured LLMs from settings
  useEffect(() => {
    const loadConfiguredLLMs = async () => {
      try {
        const llms = await getLLMs();
        setAvailableLLMs(llms);
      } catch (error) {
        console.error('Error loading LLMs:', error);
      } finally {
        setIsLoadingLLMs(false);
      }
    };
    loadConfiguredLLMs();
  }, []);

  if (!node) return null;

  const handleConfigChange = (key, value) => {
    const newConfig = { ...config, [key]: value };
    setConfig(newConfig);
    onUpdate(node.id, newConfig);
  };

  const handleKeyDown = (e) => {
    // Prevent keydown events from propagating to the parent Flow component
    // This prevents accidental node deletion when typing in input fields
    if (e.key === 'Delete' || e.key === 'Backspace') {
      e.stopPropagation();
    }
  };

  const renderConfigFields = () => {
    switch (node.type) {
      case 'agent':
        return (
          <>
            <div className="config-field">
              <label>Agent Name</label>
              <input
                type="text"
                value={config.label || ''}
                onChange={(e) => handleConfigChange('label', e.target.value)}
                placeholder="Enter agent name"
              />
            </div>
            <div className="config-field">
              <label>Description</label>
              <textarea
                value={config.description || ''}
                onChange={(e) => handleConfigChange('description', e.target.value)}
                placeholder="Describe the agent's purpose"
                rows={3}
              />
            </div>
            <div className="config-field">
              <label>Instructions</label>
              <textarea
                value={config.instructions || ''}
                onChange={(e) => handleConfigChange('instructions', e.target.value)}
                placeholder="Agent instructions and behavior"
                rows={4}
              />
            </div>
          </>
        );
      
      case 'llm':
        return null; // LLM nodes are configured in Settings page only
      
      case 'database':
        return (
          <>
            <div className="config-field">
              <label>Database Type</label>
              <select
                value={config.type || 'PostgreSQL'}
                onChange={(e) => handleConfigChange('type', e.target.value)}
              >
                <option value="PostgreSQL">PostgreSQL</option>
                <option value="MongoDB">MongoDB</option>
                <option value="MySQL">MySQL</option>
                <option value="Redis">Redis</option>
              </select>
            </div>
            <div className="config-field">
              <label>Connection String</label>
              <input
                type="password"
                value={config.connectionString || ''}
                onChange={(e) => handleConfigChange('connectionString', e.target.value)}
                placeholder="Database connection string"
              />
            </div>
            <div className="config-field">
              <label>Database Name</label>
              <input
                type="text"
                value={config.database || ''}
                onChange={(e) => handleConfigChange('database', e.target.value)}
                placeholder="Database name"
              />
            </div>
          </>
        );
    
      case 'teams':
        return (
          <>
            <div className="config-field">
              <label>Team Name</label>
              <input
                type="text"
                value={config.team || ''}
                onChange={(e) => handleConfigChange('team', e.target.value)}
                placeholder="Team name"
              />
            </div>
            <div className="config-field">
              <label>Channel</label>
              <input
                type="text"
                value={config.channel || ''}
                onChange={(e) => handleConfigChange('channel', e.target.value)}
                placeholder="Channel name"
              />
            </div>
            <div className="config-field">
              <label>Webhook URL</label>
              <input
                type="url"
                value={config.webhookUrl || ''}
                onChange={(e) => handleConfigChange('webhookUrl', e.target.value)}
                placeholder="Teams webhook URL"
              />
            </div>
          </>
        );
      
      case 'chat':
        return (
          <>
            <div className="config-field">
              <label>Chat Interface Name</label>
              <input
                type="text"
                value={config.label || ''}
                onChange={(e) => handleConfigChange('label', e.target.value)}
                placeholder="Enter chat interface name"
              />
            </div>
            <div className="config-field">
              <label>Welcome Message</label>
              <textarea
                value={config.welcomeMessage || ''}
                onChange={(e) => handleConfigChange('welcomeMessage', e.target.value)}
                placeholder="Custom welcome message for users"
                rows={2}
              />
            </div>
            <div className="config-field">
              <label>Auto-Response</label>
              <div className="checkbox-group">
                <label>
                  <input
                    type="checkbox"
                    checked={config.autoResponse || false}
                    onChange={(e) => handleConfigChange('autoResponse', e.target.checked)}
                  />
                  Enable automatic responses
                </label>
              </div>
            </div>
            <div className="config-field">
              <label>Message History Limit</label>
              <input
                type="number"
                value={config.historyLimit || 50}
                onChange={(e) => handleConfigChange('historyLimit', parseInt(e.target.value))}
                placeholder="Max messages to store"
                min="10"
                max="1000"
              />
            </div>
          </>
        );
      
      case 'orchestrator':
        return (
          <>
            <div className="config-field">
              <label>SQL File / Workflow</label>
              <input 
                ref={fileInputRef}
                type="file" 
                accept=".sql,.json" 
                onChange={async (e) => {
                  const file = e.target.files[0];
                  if (file) {
                    const reader = new FileReader();
                    reader.onload = async (event) => {
                      const content = event.target.result;
                      const fileName = file.name;
                      const fileType = fileName.endsWith('.sql') ? 'sql' : 'json';
                      
                      // If Electron, save file to config/sql/ directory
                      if (window.electronAPI && window.electronAPI.saveSqlFile) {
                        try {
                          const result = await window.electronAPI.saveSqlFile(fileName, content);
                          if (result.success) {
                            // Store reference to file - update all at once
                            const newConfig = {
                              ...config,
                              sqlFile: result.relativePath,
                              fileName: fileName,
                              fileType: fileType
                            };
                            setConfig(newConfig);
                            onUpdate(node.id, newConfig);
                          } else {
                            alert('Failed to save SQL file: ' + result.error);
                          }
                        } catch (error) {
                          alert('Error saving SQL file: ' + error.message);
                        }
                      } else {
                        // Fallback for non-Electron environment (store inline)
                        const newConfig = {
                          ...config,
                          fileContent: content,
                          fileName: fileName,
                          fileType: fileType
                        };
                        setConfig(newConfig);
                        onUpdate(node.id, newConfig);
                      }
                    };
                    reader.readAsText(file);
                  }
                }}
              />
              {config.fileName && (
                <div style={{ 
                  marginTop: '8px', 
                  padding: '8px', 
                  background: '#f0f0f0', 
                  borderRadius: '4px',
                  fontSize: '12px',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px'
                }}>
                  <span>📄 {config.fileName}</span>
                  <button 
                    onClick={(e) => {
                      e.stopPropagation();
                      e.preventDefault();
                      // Reset file input
                      if (fileInputRef.current) {
                        fileInputRef.current.value = '';
                      }
                      const newConfig = {
                        ...config,
                        fileContent: '',
                        sqlFile: '',
                        fileName: '',
                        fileType: '',
                        stepCount: null
                      };
                      setConfig(newConfig);
                      onUpdate(node.id, newConfig);
                    }}
                    style={{
                      background: 'none',
                      border: 'none',
                      color: '#ff4444',
                      cursor: 'pointer',
                      fontSize: '18px',
                      padding: '0 4px',
                      lineHeight: 1
                    }}
                    title="Remove file"
                  >
                    ×
                  </button>
                </div>
              )}
            </div>
          </>
        );
      
      default:
        return <div>No configuration available for this node type.</div>;
    }
  };

  return (
    <div className="config-panel-overlay" onClick={onClose}>
      <div className="config-panel" onClick={(e) => e.stopPropagation()} onKeyDown={handleKeyDown}>
        <div className="config-header">
          <h3>Configure {node.type.charAt(0).toUpperCase() + node.type.slice(1)} Node</h3>
          <button className="close-btn" onClick={onClose}>×</button>
        </div>
        <div className="config-content">
          {renderConfigFields()}
        </div>
        <div className="config-footer">
          <button className="btn-secondary" onClick={onClose}>Cancel</button>
          <button className="btn-primary" onClick={onClose}>Save</button>
        </div>
      </div>
    </div>
  );
};

export default NodeConfigPanel;