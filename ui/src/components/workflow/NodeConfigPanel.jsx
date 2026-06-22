import React, { useState, useEffect, useRef, useCallback } from 'react';
import PropTypes from 'prop-types';
import { localTimeToCron, cronToLocalTime } from '../../utils/cronUtils';
import { agentApiClient } from '../../services/agentApiClient';

// Compact human-readable byte size for log-group hints (e.g. 1.4 MB).
const formatStoredBytes = (bytes) => {
  if (bytes == null || Number.isNaN(bytes)) return null;
  if (bytes === 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const value = bytes / Math.pow(1024, i);
  return `${value >= 10 || i === 0 ? Math.round(value) : value.toFixed(1)} ${units[i]}`;
};

const NodeConfigPanel = ({ node, workflowName, onUpdate, onClose }) => {
  const [config, setConfig] = useState(node?.data || {});
  const fileInputRef = useRef(null);
  const [testingConnection, setTestingConnection] = useState(false);
  const [connectionStatus, setConnectionStatus] = useState(null);
  const [discoveringGroups, setDiscoveringGroups] = useState(false);
  const [discoveredGroups, setDiscoveredGroups] = useState([]);
  const [discoverPrefix, setDiscoverPrefix] = useState('');
  const [discoverError, setDiscoverError] = useState(null);
  const [discoverSearched, setDiscoverSearched] = useState(false);
  // Agent profiles + output-schema catalog (loaded once for the agent node).
  const [agentProfiles, setAgentProfiles] = useState([]);
  const [outputSchemas, setOutputSchemas] = useState([]);
  const [timeInput, setTimeInput] = useState(() => {
    // Initialize time from startTime first (most reliable), then cronExpression, or default to current time
    if (node?.data?.startTime) {
      return node.data.startTime;
    }
    if (node?.data?.cronExpression) {
      return cronToLocalTime(node.data.cronExpression);
    }
    const now = new Date();
    return `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`;
  });

  // Sync config ONLY when switching to a different node (by ID change)
  // Do NOT sync on every node.data change to prevent resetting during editing
  useEffect(() => {
    if (node?.data) {
      setConfig(node.data);

      // Also update timeInput to reflect the latest schedule
      if (node.data.startTime) {
        setTimeInput(node.data.startTime);
      } else if (node.data.cronExpression) {
        setTimeInput(cronToLocalTime(node.data.cronExpression));
      }
    }
  }, [node?.id]); // ONLY depend on node ID, not node.data

  // Load agent profiles + output-schema catalog once when configuring an agent.
  useEffect(() => {
    if (node?.type !== 'agent') return;
    let cancelled = false;
    (async () => {
      const list = await agentApiClient.listAgentProfiles().catch(() => null);
      const cat = await agentApiClient.getAgentProfileCatalog().catch(() => null);
      if (cancelled) return;
      if (list?.profiles) setAgentProfiles(list.profiles);
      if (cat?.output_schemas) setOutputSchemas(cat.output_schemas);
    })();
    return () => { cancelled = true; };
  }, [node?.id, node?.type]);

  if (!node) return null;

  const handleConfigChange = (key, value) => {
    const newConfig = { ...config, [key]: value };
    setConfig(newConfig);
    onUpdate(node.id, newConfig);
  };

  const handleDiscoverGroups = async () => {
    setDiscoveringGroups(true);
    setDiscoverError(null);
    setDiscoveredGroups([]);
    try {
      const result = await agentApiClient.discoverCloudWatchLogGroups(
        discoverPrefix || undefined,
        config.awsRegion || 'us-east-1',
        50,
        config.awsProfile || undefined,
      );
      // Keep the full metadata (size, retention) so the picker can show it.
      setDiscoveredGroups((result.log_groups || []).filter(g => g && g.name));
      setDiscoverSearched(true);
    } catch (error) {
      setDiscoveredGroups([]);
      setDiscoverSearched(true);
      setDiscoverError(
        error?.response?.data?.detail
        || error?.message
        || 'Could not reach AWS. Check the region, profile, and credentials.',
      );
    } finally {
      setDiscoveringGroups(false);
    }
  };

  // Add a single discovered group WITHOUT closing the result list, so the
  // user can pick several from one search.
  const handleAddDiscoveredGroup = (groupName) => {
    const existing = config.logGroups || [];
    if (!existing.includes(groupName)) {
      handleConfigChange('logGroups', [...existing, groupName]);
    }
  };

  const handleAddAllDiscovered = () => {
    const existing = config.logGroups || [];
    const names = discoveredGroups.map(g => g.name).filter(Boolean);
    const merged = Array.from(new Set([...existing, ...names]));
    handleConfigChange('logGroups', merged);
  };

  const handleClearDiscovered = () => {
    setDiscoveredGroups([]);
    setDiscoverSearched(false);
    setDiscoverError(null);
  };

  const handleTestConnection = async () => {
    setTestingConnection(true);
    setConnectionStatus(null);

    try {
      const result = await agentApiClient.testCloudWatchConnection(
        config.awsRegion || 'us-east-1',
        {
          awsProfile: config.awsProfile
        }
      );
      setConnectionStatus(result);
    } catch (error) {
      setConnectionStatus({
        success: false,
        message: `Connection failed: ${error.message}`
      });
    } finally {
      setTestingConnection(false);
    }
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
              <label htmlFor="agent-name">Agent Name</label>
              <input
                id="agent-name"
                type="text"
                value={config.label || ''}
                onChange={(e) => handleConfigChange('label', e.target.value)}
                placeholder="Enter agent name"
              />
            </div>
            <div className="config-field">
              <label>Agent Mode</label>
              <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginTop: '8px' }}>
                <span style={{ fontSize: '13px', color: config.agentMode === 'multi' ? '#666' : '#1976d2', fontWeight: config.agentMode !== 'multi' ? '600' : 'normal' }}>
                  Single
                </span>
                <div
                  className="toggle-switch"
                  onClick={() => handleConfigChange('agentMode', config.agentMode === 'multi' ? 'single' : 'multi')}
                  style={{
                    position: 'relative',
                    width: '48px',
                    height: '24px',
                    backgroundColor: config.agentMode === 'multi' ? '#1976d2' : '#ccc',
                    borderRadius: '12px',
                    cursor: 'pointer',
                    transition: 'background-color 0.2s'
                  }}
                >
                  <div
                    style={{
                      position: 'absolute',
                      top: '2px',
                      left: config.agentMode === 'multi' ? '26px' : '2px',
                      width: '20px',
                      height: '20px',
                      backgroundColor: 'white',
                      borderRadius: '50%',
                      transition: 'left 0.2s',
                      boxShadow: '0 1px 3px rgba(0,0,0,0.3)'
                    }}
                  />
                </div>
                <span style={{ fontSize: '13px', color: config.agentMode === 'multi' ? '#1976d2' : '#666', fontWeight: config.agentMode === 'multi' ? '600' : 'normal' }}>
                  Multi
                </span>
              </div>
              <small style={{ color: '#666', marginTop: '8px', display: 'block' }}>
                {config.agentMode === 'multi'
                  ? '🧩 Uses orchestrator to coordinate multiple specialized agents'
                  : '🤖 One agent handles all tasks'}
              </small>
            </div>
            <div className="config-field">
              <label htmlFor="agent-description">Description</label>
              <textarea
                id="agent-description"
                value={config.description || ''}
                onChange={(e) => handleConfigChange('description', e.target.value)}
                placeholder="Describe the agent's purpose"
                rows={3}
              />
            </div>
            <div className="config-field">
              <label htmlFor="agent-profile">Agent Profile</label>
              <select
                id="agent-profile"
                value={config.profile || ''}
                onChange={(e) => handleConfigChange('profile', e.target.value)}
              >
                <option value="">— None (investigation default) —</option>
                {agentProfiles.map((p) => (
                  <option key={p.name} value={p.name}>
                    {p.name}{p.builtin ? ' (builtin)' : ''}
                  </option>
                ))}
              </select>
              <small style={{ color: '#666', fontSize: '11px' }}>
                Pick a profile to configure what type of agent this is (role, output
                shape, policies). Fields you set below override the profile.
              </small>
            </div>
            <div className="config-field">
              <label htmlFor="agent-output-schema">Structured Output</label>
              <select
                id="agent-output-schema"
                value={config.outputSchema || ''}
                onChange={(e) => {
                  const v = e.target.value;
                  // Selecting a schema implies structured mode; clearing reverts to text.
                  const newConfig = {
                    ...config,
                    outputSchema: v,
                    outputMode: v ? 'structured' : 'text',
                  };
                  setConfig(newConfig);
                  onUpdate(node.id, newConfig);
                }}
              >
                <option value="">Text only (no structured output)</option>
                {outputSchemas.map((s) => (
                  <option key={s} value={s}>{s}</option>
                ))}
              </select>
              <small style={{ color: '#666', fontSize: '11px' }}>
                Return a validated JSON report in this shape alongside the prose.
              </small>
            </div>
            <div className="config-field">
              <label htmlFor="agent-role-prompt">Role Prompt (override)</label>
              <textarea
                id="agent-role-prompt"
                value={config.rolePrompt || ''}
                onChange={(e) => handleConfigChange('rolePrompt', e.target.value)}
                placeholder="Optional: replace the agent's role sentence entirely"
                rows={2}
              />
            </div>
            <div className="config-field">
              <label htmlFor="agent-instructions">Instructions</label>
              <textarea
                id="agent-instructions"
                value={config.instructions || ''}
                onChange={(e) => handleConfigChange('instructions', e.target.value)}
                placeholder="Agent instructions and behavior"
                rows={4}
              />
            </div>
            <div className="config-field">
              <label>Deep-agent capabilities</label>
              <label className="checkbox-label" style={{ display: 'block', marginTop: 4 }}>
                <input
                  type="checkbox"
                  checked={!!config.planning}
                  onChange={(e) => handleConfigChange('planning', e.target.checked)}
                />{' '}Planning — give the agent a write_todos / update_todo task list
              </label>
              <label className="checkbox-label" style={{ display: 'block', marginTop: 4 }}>
                <input
                  type="checkbox"
                  checked={!!config.filesystem}
                  onChange={(e) => handleConfigChange('filesystem', e.target.checked)}
                />{' '}Scratch filesystem — fs_write/read/grep for context offload
              </label>
              <label className="checkbox-label" style={{ display: 'block', marginTop: 4 }}>
                <input
                  type="checkbox"
                  checked={!!config.autoLearn}
                  onChange={(e) => handleConfigChange('autoLearn', e.target.checked)}
                />{' '}Auto-learn — distill skills/memory from each run (opt-in)
              </label>
              <label className="checkbox-label" style={{ display: 'block', marginTop: 4 }}>
                <input
                  type="checkbox"
                  checked={!!config.sandbox}
                  onChange={(e) => handleConfigChange('sandbox', e.target.checked)}
                />{' '}Sandbox — isolated run_command shell
              </label>
              {config.sandbox && (
                <small style={{ color: '#92400e', fontSize: '11px', display: 'block', marginTop: 2 }}>
                  Requires a sandbox backend configured on the server (SANDBOX_BACKEND); otherwise this is a no-op.
                </small>
              )}
            </div>
            <div className="config-field">
              <label htmlFor="agent-subagents">Subagents (advanced, JSON)</label>
              <textarea
                id="agent-subagents"
                value={
                  typeof config.subagents === 'string'
                    ? config.subagents
                    : (config.subagents ? JSON.stringify(config.subagents, null, 2) : '')
                }
                onChange={(e) => handleConfigChange('subagents', e.target.value)}
                placeholder='[{"name":"code-specialist","role_prompt":"…","tools":["crawler_*"]}]'
                rows={3}
                style={{ fontFamily: 'monospace', fontSize: 12 }}
              />
              <small style={{ color: '#666', fontSize: '11px' }}>
                Each entry becomes a delegate_to_&lt;name&gt; tool. Leave blank for none.
              </small>
            </div>
          </>
        );

      case 'llm':
        return null; // LLM nodes are configured in Settings page only

      case 'database':
        return (
          <>
            <div className="config-field">
              <label htmlFor="db-type">Database Type</label>
              <select
                id="db-type"
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
              <label htmlFor="db-connection">Connection String</label>
              <input
                id="db-connection"
                type="password"
                value={config.connectionString || ''}
                onChange={(e) => handleConfigChange('connectionString', e.target.value)}
                placeholder="Database connection string"
              />
            </div>
            <div className="config-field">
              <label htmlFor="db-name">Database Name</label>
              <input
                id="db-name"
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
              <label htmlFor="team-name">Team Name</label>
              <input
                id="team-name"
                type="text"
                value={config.team || ''}
                onChange={(e) => handleConfigChange('team', e.target.value)}
                placeholder="Team name"
              />
            </div>
            <div className="config-field">
              <label htmlFor="team-channel">Channel</label>
              <input
                id="team-channel"
                type="text"
                value={config.channel || ''}
                onChange={(e) => handleConfigChange('channel', e.target.value)}
                placeholder="Channel name"
              />
            </div>
            <div className="config-field">
              <label htmlFor="team-webhook">Webhook URL</label>
              <input
                id="team-webhook"
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
              <label htmlFor="chat-name">Chat Interface Name</label>
              <input
                id="chat-name"
                type="text"
                value={config.label || ''}
                onChange={(e) => handleConfigChange('label', e.target.value)}
                placeholder="Enter chat interface name"
              />
            </div>
            <div className="config-field">
              <label htmlFor="chat-welcome">Welcome Message</label>
              <textarea
                id="chat-welcome"
                value={config.welcomeMessage || ''}
                onChange={(e) => handleConfigChange('welcomeMessage', e.target.value)}
                placeholder="Custom welcome message for users"
                rows={2}
              />
            </div>
            <div className="config-field">
              <label htmlFor="chat-auto-response">Auto-Response</label>
              <div className="checkbox-group">
                <label htmlFor="chat-auto-response-checkbox">
                  <input
                    id="chat-auto-response-checkbox"
                    type="checkbox"
                    checked={config.autoResponse || false}
                    onChange={(e) => handleConfigChange('autoResponse', e.target.checked)}
                  />
                  {' '}Enable automatic responses
                </label>
              </div>
            </div>
            <div className="config-field">
              <label htmlFor="chat-history-limit">Message History Limit</label>
              <input
                id="chat-history-limit"
                type="number"
                value={config.historyLimit || 50}
                onChange={(e) => handleConfigChange('historyLimit', Number.parseInt(e.target.value, 10))}
                placeholder="Max messages to store"
                min="10"
                max="1000"
              />
            </div>
          </>
        );

      case 'orchestrator':
        return (
          <div className="config-field">
            <label htmlFor="orchestrator-file">SQL File / Workflow</label>
            <input
              id="orchestrator-file"
              ref={fileInputRef}
              type="file"
              accept=".sql,.json"
              onChange={async (e) => {
                const file = e.target.files[0];
                if (file) {
                  try {
                    const fileName = file.name;
                    const fileType = fileName.endsWith('.sql') ? 'sql' : 'json';
                    const content = await file.text();

                    // Store file content inline - will be uploaded when workflow is saved
                    const newConfig = {
                      ...config,
                      sqlFile: fileName,
                      fileName: fileName,
                      fileType: fileType,
                      fileContent: content,
                      pendingUpload: true  // Flag to indicate this needs to be uploaded
                    };
                    setConfig(newConfig);
                    onUpdate(node.id, newConfig);
                    console.log('SQL file loaded, will be uploaded when workflow is saved');
                  } catch (error) {
                    alert('Error reading file: ' + error.message);
                  }
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
        );

      case 'scheduler':
        return (
          <>
            <div className="config-field">
              <label htmlFor="scheduler-name">Schedule Name</label>
              <input
                id="scheduler-name"
                type="text"
                value={config.label || ''}
                onChange={(e) => handleConfigChange('label', e.target.value)}
                placeholder="Enter schedule name"
              />
            </div>

            <div className="config-field">
              <label htmlFor="scheduler-recurrence">Recurrence</label>
              <select
                id="scheduler-recurrence"
                value={config.recurrence || 'daily'}
                onChange={(e) => {
                  const recurrence = e.target.value;

                  // Use utility function to generate cron from local time
                  const cronExpression = localTimeToCron(timeInput, recurrence);

                  // Update recurrence, cron (UTC), AND preserve startTime (local)
                  const newConfig = {
                    ...config,
                    recurrence: recurrence,
                    cronExpression: cronExpression,
                    startTime: timeInput
                  };
                  setConfig(newConfig);
                  onUpdate(node.id, newConfig);
                }}
              >
                <option value="daily">Daily</option>
                <option value="weekly">Weekly</option>
                <option value="monthly">Monthly</option>
              </select>
            </div>

            <div className="config-field">
              <label htmlFor="scheduler-time">Time</label>
              <input
                id="scheduler-time"
                type="time"
                value={timeInput}
                onChange={(e) => {
                  const newTime = e.target.value;
                  setTimeInput(newTime);

                  // Use utility function to generate cron from local time
                  const recurrence = config.recurrence || 'daily';
                  const cronExpression = localTimeToCron(newTime, recurrence);

                  // Update both cronExpression (UTC) and startTime (local for display)
                  const newConfig = {
                    ...config,
                    cronExpression: cronExpression,
                    startTime: newTime
                  };
                  setConfig(newConfig);
                  onUpdate(node.id, newConfig);
                }}
              />
            </div>
          </>
        );

      case 'cloudwatchAnalyzer':
        return (
          <>
            <div className="config-field">
              <label htmlFor="analyzer-name">Analyzer Name</label>
              <input
                id="analyzer-name"
                type="text"
                value={config.label || ''}
                onChange={(e) => handleConfigChange('label', e.target.value)}
                placeholder="Enter analyzer name"
              />
            </div>

            <div className="config-field">
              <label htmlFor="aws-region">AWS Region</label>
              <input
                id="aws-region"
                type="text"
                value={config.awsRegion || ''}
                onChange={(e) => handleConfigChange('awsRegion', e.target.value)}
                placeholder="us-east-1"
              />
            </div>

            <div className="config-field">
              <label htmlFor="aws-profile">AWS Profile</label>
              <input
                id="aws-profile"
                type="text"
                value={config.awsProfile || ''}
                onChange={(e) => handleConfigChange('awsProfile', e.target.value)}
                placeholder="default"
              />
              <small style={{ color: '#666', fontSize: '11px' }}>
                AWS profile name from ~/.aws/credentials
              </small>
            </div>

            <div className="config-field">
              <button
                type="button"
                onClick={handleTestConnection}
                disabled={testingConnection}
                style={{
                  background: testingConnection ? '#9e9e9e' : '#2196f3',
                  color: 'white',
                  border: 'none',
                  borderRadius: '4px',
                  cursor: testingConnection ? 'not-allowed' : 'pointer',
                  padding: '10px 16px',
                  width: '100%',
                  fontSize: '13px',
                  fontWeight: 500,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  gap: '8px'
                }}
              >
                {testingConnection ? 'Testing...' : 'Test Connection'}
              </button>
              {connectionStatus && (
                <div style={{
                  marginTop: '10px',
                  padding: '10px',
                  borderRadius: '4px',
                  background: connectionStatus.success ? '#e8f5e9' : '#ffebee',
                  border: `1px solid ${connectionStatus.success ? '#4caf50' : '#f44336'}`,
                  fontSize: '12px'
                }}>
                  <div style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '6px',
                    color: connectionStatus.success ? '#2e7d32' : '#c62828'
                  }}>
                    <span style={{ fontSize: '16px' }}>
                      {connectionStatus.success ? '✓' : '✕'}
                    </span>
                    <span>{connectionStatus.message}</span>
                  </div>
                </div>
              )}
            </div>

            <div className="config-field">
              <label>Log Groups</label>
              <div className="log-groups-list" style={{ maxHeight: '200px', overflowY: 'auto', marginBottom: '10px' }}>
                {(config.logGroups || []).map((group, index) => (
                  <div key={index} className="log-group-item" style={{ display: 'flex', gap: '5px', marginBottom: '5px' }}>
                    <input
                      type="text"
                      value={group}
                      onChange={(e) => {
                        const newGroups = [...(config.logGroups || [])];
                        newGroups[index] = e.target.value;
                        handleConfigChange('logGroups', newGroups);
                      }}
                      placeholder="/aws/lambda/my-function"
                      style={{ flex: 1 }}
                    />
                    <button
                      className="remove-btn"
                      onClick={() => {
                        const newGroups = config.logGroups.filter((_, i) => i !== index);
                        handleConfigChange('logGroups', newGroups);
                      }}
                      style={{
                        background: '#f44336',
                        color: 'white',
                        border: 'none',
                        borderRadius: '4px',
                        cursor: 'pointer',
                        padding: '4px 8px'
                      }}
                    >✕</button>
                  </div>
                ))}
              </div>
              <button
                className="add-btn"
                onClick={() => handleConfigChange('logGroups', [...(config.logGroups || []), ''])}
                style={{
                  background: '#4caf50',
                  color: 'white',
                  border: 'none',
                  borderRadius: '4px',
                  cursor: 'pointer',
                  padding: '8px 12px',
                  width: '100%',
                }}
              >+ Add manually</button>

              {/* ── Discover from AWS ───────────────────────────────────── */}
              {/* Step 1: type an optional prefix.  Step 2: Discover.        */}
              {/* Results stay open so several groups can be added at once.  */}
              <div style={{ marginTop: '12px', paddingTop: '12px', borderTop: '1px dashed #ddd' }}>
                <div style={{ fontSize: '11px', color: '#666', marginBottom: '6px' }}>
                  Don&apos;t know the exact path? Discover log groups from AWS:
                </div>
                <div style={{ display: 'flex', gap: '6px' }}>
                  <input
                    type="text"
                    value={discoverPrefix}
                    onChange={(e) => setDiscoverPrefix(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' && !discoveringGroups) {
                        e.preventDefault();
                        handleDiscoverGroups();
                      }
                    }}
                    placeholder="Filter by prefix, e.g. /aws/lambda/kyc- (blank = all)"
                    style={{ flex: 1, fontSize: '12px', padding: '6px 8px', boxSizing: 'border-box' }}
                  />
                  <button
                    type="button"
                    onClick={handleDiscoverGroups}
                    disabled={discoveringGroups}
                    style={{
                      background: discoveringGroups ? '#9e9e9e' : '#7b1fa2',
                      color: 'white',
                      border: 'none',
                      borderRadius: '4px',
                      cursor: discoveringGroups ? 'not-allowed' : 'pointer',
                      padding: '6px 12px',
                      fontSize: '12px',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {discoveringGroups ? 'Discovering…' : '🔍 Discover'}
                  </button>
                </div>

                {/* Error state */}
                {discoverError && (
                  <div style={{
                    marginTop: '6px',
                    padding: '6px 8px',
                    fontSize: '12px',
                    color: '#c62828',
                    background: '#fdecea',
                    border: '1px solid #f5c6cb',
                    borderRadius: '4px',
                  }}>
                    ⚠ {discoverError}
                  </div>
                )}

                {/* Empty state — searched, no error, nothing found */}
                {!discoveringGroups && discoverSearched && !discoverError && discoveredGroups.length === 0 && (
                  <div style={{ marginTop: '6px', fontSize: '12px', color: '#777' }}>
                    No log groups found{discoverPrefix ? ` matching "${discoverPrefix}"` : ''} in {config.awsRegion || 'us-east-1'}.
                  </div>
                )}

                {/* Discovered groups list */}
                {discoveredGroups.length > 0 && (
                  <div style={{
                    marginTop: '6px',
                    border: '1px solid #ccc',
                    borderRadius: '4px',
                    background: '#fff',
                  }}>
                    <div style={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      padding: '4px 8px',
                      fontSize: '11px',
                      color: '#666',
                      borderBottom: '1px solid #eee',
                    }}>
                      <span>{discoveredGroups.length} found — click to add</span>
                      <span>
                        <button
                          type="button"
                          onClick={handleAddAllDiscovered}
                          style={{
                            background: 'none', border: 'none', color: '#7b1fa2',
                            cursor: 'pointer', fontSize: '11px', padding: '0 6px',
                          }}
                        >Add all</button>
                        <button
                          type="button"
                          onClick={handleClearDiscovered}
                          style={{
                            background: 'none', border: 'none', color: '#999',
                            cursor: 'pointer', fontSize: '11px', padding: '0 4px',
                          }}
                        >Clear</button>
                      </span>
                    </div>
                    <div style={{ maxHeight: '180px', overflowY: 'auto' }}>
                      {discoveredGroups.map((g) => {
                        const added = (config.logGroups || []).includes(g.name);
                        const size = formatStoredBytes(g.stored_bytes);
                        const retention = g.retention_days ? `${g.retention_days}d retention` : 'never expires';
                        return (
                          <div
                            key={g.name}
                            onClick={() => !added && handleAddDiscoveredGroup(g.name)}
                            title={added ? 'Already added' : 'Click to add'}
                            style={{
                              display: 'flex',
                              alignItems: 'center',
                              justifyContent: 'space-between',
                              gap: '8px',
                              padding: '6px 8px',
                              cursor: added ? 'default' : 'pointer',
                              fontSize: '12px',
                              borderBottom: '1px solid #f5f5f5',
                              opacity: added ? 0.55 : 1,
                            }}
                            onMouseOver={(e) => { if (!added) e.currentTarget.style.background = '#e3f2fd'; }}
                            onMouseOut={(e) => { e.currentTarget.style.background = ''; }}
                          >
                            <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                              {g.name}
                              <span style={{ display: 'block', fontSize: '10px', color: '#999' }}>
                                {[size, retention].filter(Boolean).join(' · ')}
                              </span>
                            </span>
                            <span style={{ fontSize: '11px', color: added ? '#2e7d32' : '#7b1fa2', whiteSpace: 'nowrap' }}>
                              {added ? '✓ added' : '+ add'}
                            </span>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}
              </div>
            </div>

            <div className="config-field">
              <label htmlFor="analysis-type">Analysis Type</label>
              <select
                id="analysis-type"
                value={config.analysisType || 'error-patterns'}
                onChange={(e) => handleConfigChange('analysisType', e.target.value)}
              >
                <option value="error-patterns">Error Patterns</option>
                <option value="activity-summary">Activity Summary</option>
                <option value="anomaly-detection">Anomaly Detection</option>
                <option value="correlation">Cross-Group Correlation</option>
                <option value="metrics">CloudWatch Metrics</option>
                <option value="alarms">CloudWatch Alarms</option>
                <option value="custom-query">Custom Insights Query</option>
              </select>
            </div>

            <div className="config-field">
              <label htmlFor="analysis-depth">Analysis Depth</label>
              <select
                id="analysis-depth"
                value={config.analysisDepth || 'auto'}
                onChange={(e) => handleConfigChange('analysisDepth', e.target.value)}
              >
                <option value="auto">Auto — drill down only when it matters (recommended)</option>
                <option value="shallow">Shallow — triage + summary only</option>
                <option value="deep">Deep — always drill down + correlate</option>
              </select>
              <small style={{ color: '#666', fontSize: '11px' }}>
                All modes run parallel triage (alarms, anomalies, error patterns) and
                always end with a structured report. Auto adds targeted drill-down only
                on high/critical severity, weak evidence, or a firing alarm. Shallow skips
                drill-down (cheapest); Deep always drills the top findings and correlates
                across services (slower, higher cost).
              </small>
            </div>

            <div className="config-field">
              <label htmlFor="tool-mode">Tool Mode</label>
              <select
                id="tool-mode"
                value={config.toolMode || 'auto'}
                onChange={(e) => handleConfigChange('toolMode', e.target.value)}
              >
                <option value="auto">Auto — pre-scan for investigations, skip for chit-chat (recommended)</option>
                <option value="agent">Agent-driven — no pre-scan; the agent calls CloudWatch tools itself</option>
                <option value="prescan">Always pre-scan — run the deterministic scan every turn</option>
              </select>
              <small style={{ color: '#666', fontSize: '11px' }}>
                Auto skips the AWS scan when the message is a greeting or small talk, so the
                agent just replies. Agent-driven lets the model decide when to query logs.
                Always pre-scan keeps the legacy behaviour.
              </small>
            </div>

            {/* Custom query textarea — shown only for custom-query type */}
            {config.analysisType === 'custom-query' && (
              <div className="config-field">
                <label htmlFor="custom-insights-query">Insights Query</label>
                <textarea
                  id="custom-insights-query"
                  rows={5}
                  value={config.customInsightsQuery || ''}
                  onChange={(e) => handleConfigChange('customInsightsQuery', e.target.value)}
                  placeholder={`fields @timestamp, @message\n| filter @message like /error/\n| sort @timestamp desc\n| limit 100`}
                  style={{ width: '100%', fontFamily: 'monospace', fontSize: '12px', resize: 'vertical', boxSizing: 'border-box' }}
                />
                <small style={{ color: '#666', fontSize: '11px' }}>CloudWatch Logs Insights query syntax</small>
              </div>
            )}

            {/* Alarm filter — shown only for alarms type */}
            {config.analysisType === 'alarms' && (
              <>
                {/* ── Active Alarms Only shortcut ────────────────────────────── */}
                <div
                  className="config-field checkbox-field"
                  style={{
                    background: config.activeAlarmsOnly ? '#fff3e0' : undefined,
                    borderRadius: 6,
                    padding: config.activeAlarmsOnly ? '8px 10px' : undefined,
                    border: config.activeAlarmsOnly ? '1px solid #ffb74d' : undefined,
                  }}
                >
                  <label className="checkbox-label">
                    <input
                      type="checkbox"
                      checked={config.activeAlarmsOnly || false}
                      onChange={(e) => {
                        const checked = e.target.checked;
                        // Batch both keys into one config snapshot so neither
                        // overwrites the other via stale closure.
                        const newConfig = {
                          ...config,
                          activeAlarmsOnly: checked,
                          alarmStateFilter: checked ? 'ALARM' : '',
                        };
                        setConfig(newConfig);
                        onUpdate(node.id, newConfig);
                      }}
                    />
                    <span>🔴 Active Alarms Only</span>
                  </label>
                  <small style={{ color: '#e65100', fontSize: '11px', marginTop: '4px', display: 'block' }}>
                    {config.activeAlarmsOnly
                      ? 'Only alarms currently in ALARM (firing) state will be analyzed'
                      : 'Check this to restrict analysis to actively firing alarms'}
                  </small>
                </div>

                {/* Full state dropdown — hidden when "Active Alarms Only" is on */}
                {!config.activeAlarmsOnly && (
                  <div className="config-field">
                    <label htmlFor="alarm-state-filter">Alarm State Filter</label>
                    <select
                      id="alarm-state-filter"
                      value={config.alarmStateFilter || ''}
                      onChange={(e) => handleConfigChange('alarmStateFilter', e.target.value)}
                    >
                      <option value="">All states</option>
                      <option value="ALARM">ALARM (firing)</option>
                      <option value="OK">OK</option>
                      <option value="INSUFFICIENT_DATA">Insufficient Data</option>
                    </select>
                  </div>
                )}
              </>
            )}

            <div className="config-field">
              <label htmlFor="time-range">Time Range</label>
              <select
                id="time-range"
                value={config.timeRange || '1h'}
                onChange={(e) => handleConfigChange('timeRange', e.target.value)}
              >
                <option value="15m">Last 15 minutes</option>
                <option value="1h">Last 1 hour</option>
                <option value="6h">Last 6 hours</option>
                <option value="24h">Last 24 hours</option>
                <option value="7d">Last 7 days</option>
              </select>
            </div>

            <div className="config-field">
              <label htmlFor="error-threshold">Error Threshold</label>
              <input
                id="error-threshold"
                type="number"
                value={config.errorThreshold || 10}
                onChange={(e) => handleConfigChange('errorThreshold', parseInt(e.target.value))}
                placeholder="10"
                min="1"
              />
              <small style={{ color: '#666', fontSize: '11px' }}>Alert if error count exceeds this value</small>
            </div>

            <div className="config-field checkbox-field">
              <label className="checkbox-label">
                <input
                  type="checkbox"
                  checked={config.enableAlerts || false}
                  onChange={(e) => handleConfigChange('enableAlerts', e.target.checked)}
                />
                <span>Enable Alerts</span>
              </label>
            </div>
          </>
        );

      case 'codeAnalyzer':
        return (
          <CodeAnalyzerConfig config={config} handleConfigChange={handleConfigChange} />
        );

      default:
        return <div>No configuration available for this node type.</div>;
    }
  };

  return (
    <div
      className="config-panel-overlay"
      onClick={onClose}
      onKeyDown={(e) => e.key === 'Escape' && onClose()}
      tabIndex={-1}
      aria-hidden="true"
    >
      <div
        className="config-panel"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={handleKeyDown}
      >
        <div className="config-header">
          <h3 id="config-panel-title">Configure {node.type.charAt(0).toUpperCase() + node.type.slice(1)} Node</h3>
          <button className="close-btn" onClick={onClose} aria-label="Close">×</button>
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

// ─────────────────────────────────────────────────────────────────────────────
// Code Analyzer config sub-component
// ─────────────────────────────────────────────────────────────────────────────

// Repository name/path validators were removed when the manual entry block
// was dropped — repositories are now selected exclusively via the discovery
// panel below, which sources name + container path + suggested language from
// the backend's filesystem scan. There are no user-typed paths to validate.

// ── Repository discovery cache (module scope) ──────────────────────────────
// Survives open/close of the Configure CodeAnalyzer Node panel so re-opens
// render instantly from cache instead of showing a spinner every time. A
// background refresh runs after each render to keep the cache fresh
// (stale-while-revalidate). Cleared on full page reload — fine for our
// scale.
const REPO_DISCOVERY_CACHE_TTL_MS = 5 * 60 * 1000; // 5 minutes
const _repoDiscoveryCache = {
  data: null,        // { basePath, baseExists, repos[] }
  fetchedAt: 0,      // performance.now() of last successful fetch
  inflight: null,    // de-dupe concurrent fetches across multiple panel opens
};

function _isCacheFresh() {
  return (
    _repoDiscoveryCache.data !== null
    && (performance.now() - _repoDiscoveryCache.fetchedAt) < REPO_DISCOVERY_CACHE_TTL_MS
  );
}

async function _fetchRepoDiscovery({ refresh = false } = {}) {
  // De-dupe: if a fetch is already in flight, await the same promise instead
  // of firing a second request (e.g. when the panel is opened twice quickly).
  if (!refresh && _repoDiscoveryCache.inflight) {
    return _repoDiscoveryCache.inflight;
  }
  const promise = (async () => {
    const data = await agentApiClient.listCodeAnalyzerRepos({ refresh });
    const normalised = {
      basePath: data?.base_path || '',
      baseExists: Boolean(data?.base_exists),
      repos: Array.isArray(data?.repos) ? data.repos : [],
    };
    _repoDiscoveryCache.data = normalised;
    _repoDiscoveryCache.fetchedAt = performance.now();
    return normalised;
  })();
  _repoDiscoveryCache.inflight = promise;
  try {
    return await promise;
  } finally {
    _repoDiscoveryCache.inflight = null;
  }
}

function CodeAnalyzerConfig({ config, handleConfigChange }) {
  const repos = config.repos || [];

  // ── Filesystem discovery (GET /api/v1/code-analyzer/repos) ─────────────
  // Stale-while-revalidate: render from the module cache immediately if any
  // data is present, then fire a background refresh.
  const [discovery, setDiscovery] = useState(() => {
    if (_repoDiscoveryCache.data) {
      return {
        loading: false,
        revalidating: !_isCacheFresh(),
        error: null,
        ..._repoDiscoveryCache.data,
      };
    }
    return {
      loading: true,
      revalidating: false,
      error: null,
      basePath: '',
      baseExists: true,
      repos: [],
    };
  });

  const refreshDiscovery = useCallback(async (force = false) => {
    setDiscovery((prev) => ({
      ...prev,
      revalidating: prev.repos.length > 0 || force,
      loading: prev.repos.length === 0 && !force,
      error: null,
    }));
    try {
      const data = await _fetchRepoDiscovery({ refresh: force });
      setDiscovery({ loading: false, revalidating: false, error: null, ...data });
    } catch (err) {
      setDiscovery((prev) => ({
        ...prev,
        loading: false,
        revalidating: false,
        error: err?.response?.data?.detail || err?.message || 'Failed to load',
      }));
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (_isCacheFresh()) return;
      try {
        const data = await _fetchRepoDiscovery();
        if (cancelled) return;
        setDiscovery({ loading: false, revalidating: false, error: null, ...data });
      } catch (err) {
        if (cancelled) return;
        setDiscovery((prev) => ({
          ...prev,
          loading: false,
          revalidating: false,
          error: err?.response?.data?.detail || err?.message || 'Failed to load',
        }));
      }
    })();
    return () => { cancelled = true; };
  }, []);

  // ── Indexed-repos picker (GET /api/v1/crawler/repos) ─────────────────────
  // Mirrors the CloudWatch "Discover" pattern: click a button, results appear
  // in a scrollable dropdown, click a repo name to add it to the selected list.
  const [indexedRepos, setIndexedRepos] = useState([]);
  const [indexedLoading, setIndexedLoading] = useState(false);
  const [indexedError, setIndexedError] = useState(null);
  const [showIndexed, setShowIndexed] = useState(false);

  const handleDiscoverIndexed = async () => {
    setIndexedLoading(true);
    setIndexedError(null);
    setIndexedRepos([]);
    setShowIndexed(false);
    try {
      const data = await agentApiClient.listCrawlerRepos();
      setIndexedRepos(Array.isArray(data?.repos) ? data.repos : []);
      setShowIndexed(true);
    } catch (err) {
      setIndexedError(err?.response?.data?.detail || err?.message || 'Failed to load indexed repos');
    } finally {
      setIndexedLoading(false);
    }
  };

  const handleAddIndexedRepo = (indexedRepo) => {
    const name = indexedRepo.repo_name;
    if (repos.some((r) => r.name === name)) return;
    handleConfigChange('repos', [
      ...repos,
      { name, path: name, language: 'python' },
    ]);
  };

  // ── Shared helpers ────────────────────────────────────────────────────────
  const addDiscoveredRepo = (discovered) => {
    if (repos.some((r) => r.path === discovered.path)) return;
    handleConfigChange('repos', [
      ...repos,
      {
        name: discovered.name,
        path: discovered.path,
        language: discovered.suggested_language || 'python',
      },
    ]);
  };

  const removeRepo = (nameOrPath) => {
    handleConfigChange(
      'repos',
      repos.filter((r) => r.path !== nameOrPath && r.name !== nameOrPath),
    );
  };

  const isAlreadyAdded = (nameOrPath) =>
    repos.some((r) => r.path === nameOrPath || r.name === nameOrPath);

  return (
    <>
      <div className="config-field">
        <label htmlFor="ca-label">Label</label>
        <input
          id="ca-label"
          type="text"
          value={config.label || ''}
          onChange={(e) => handleConfigChange('label', e.target.value)}
          placeholder="Code Analyzer"
        />
      </div>

      {/* ── Available Repositories (indexed by crawler) ─────────────────── */}
      <div className="config-field">
        <label>Available Repositories</label>
        <div style={{ display: 'flex', gap: 6, marginBottom: 6 }}>
          <button
            type="button"
            onClick={handleDiscoverIndexed}
            disabled={indexedLoading}
            style={{
              background: indexedLoading ? '#9e9e9e' : '#7b1fa2',
              color: 'white',
              border: 'none',
              borderRadius: 4,
              cursor: indexedLoading ? 'not-allowed' : 'pointer',
              padding: '8px 12px',
              flex: 1,
              fontSize: 12,
            }}
          >
            {indexedLoading ? 'Loading…' : '🔍 Discover Indexed Repos'}
          </button>
        </div>

        {indexedError && (
          <div style={{
            fontSize: 12,
            color: '#c62828',
            background: '#ffebee',
            border: '1px solid #ef9a9a',
            borderRadius: 4,
            padding: '6px 10px',
            marginBottom: 6,
          }}>
            {indexedError}
          </div>
        )}

        {showIndexed && indexedRepos.length === 0 && !indexedLoading && (
          <div style={{ fontSize: 12, color: '#666', padding: '6px 8px' }}>
            No indexed repositories found. Index a repo via the crawler first.
          </div>
        )}

        {showIndexed && indexedRepos.length > 0 && (
          <div style={{
            marginTop: 4,
            border: '1px solid #ccc',
            borderRadius: 4,
            maxHeight: 180,
            overflowY: 'auto',
            background: '#fff',
          }}>
            <div style={{ padding: '4px 8px', fontSize: 11, color: '#666', borderBottom: '1px solid #eee' }}>
              Click to add to selected repositories
            </div>
            {indexedRepos.map((r) => {
              const added = isAlreadyAdded(r.repo_name);
              return (
                <div
                  key={r.repo_name}
                  onClick={() => !added && handleAddIndexedRepo(r)}
                  style={{
                    padding: '6px 10px',
                    cursor: added ? 'default' : 'pointer',
                    fontSize: 12,
                    borderBottom: '1px solid #f5f5f5',
                    background: added ? '#f5f5f5' : '#fff',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    gap: 8,
                  }}
                  onMouseOver={(e) => { if (!added) e.currentTarget.style.background = '#e3f2fd'; }}
                  onMouseOut={(e) => { if (!added) e.currentTarget.style.background = added ? '#f5f5f5' : '#fff'; }}
                >
                  <div style={{ minWidth: 0 }}>
                    <span style={{ fontWeight: 500, color: added ? '#999' : '#222' }}>
                      {r.repo_name}
                    </span>
                    {r.files_indexed != null && (
                      <span style={{ marginLeft: 6, fontSize: 11, color: '#888' }}>
                        {r.files_indexed} files
                      </span>
                    )}
                    {r.generated_at && (
                      <div style={{ fontSize: 10, color: '#aaa', marginTop: 1 }}>
                        indexed {new Date(r.generated_at).toLocaleDateString()}
                      </div>
                    )}
                  </div>
                  {added ? (
                    <span style={{ fontSize: 11, color: '#999' }}>✓ added</span>
                  ) : (
                    <span style={{ fontSize: 11, color: '#1565c0' }}>+ Add</span>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* ── Filesystem repos (REPOS_BASE_PATH) ──────────────────────────── */}
      <div className="config-field">
        <label style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span>Filesystem Repos</span>
          <span style={{ color: '#666', fontWeight: 'normal', fontSize: 11, flex: 1 }}>
            (under {discovery.basePath || 'REPOS_BASE_PATH'})
          </span>
          {discovery.revalidating && (
            <span
              title="Refreshing in the background…"
              style={{ color: '#888', fontSize: 11, fontWeight: 'normal' }}
            >
              ⟳ syncing
            </span>
          )}
          <button
            type="button"
            onClick={() => refreshDiscovery(true)}
            disabled={discovery.loading || discovery.revalidating}
            style={{
              background: '#f5f5f5',
              border: '1px solid #d0d0d0',
              borderRadius: 4,
              cursor: discovery.loading || discovery.revalidating ? 'wait' : 'pointer',
              color: '#444',
              padding: '2px 8px',
              fontSize: 11,
              fontWeight: 'normal',
            }}
            title="Force a fresh scan (bypasses the 60s server cache)"
          >
            ↻ Refresh
          </button>
        </label>

        {discovery.loading && (
          <div style={{ fontSize: 12, color: '#666', padding: '6px 8px' }}>
            Loading filesystem repositories…
          </div>
        )}

        {!discovery.loading && discovery.error && (
          <div style={{
            fontSize: 12,
            color: '#c62828',
            background: '#ffebee',
            border: '1px solid #ef9a9a',
            borderRadius: 4,
            padding: '6px 10px',
          }}>
            Could not load filesystem repos: {discovery.error}
          </div>
        )}

        {!discovery.loading && !discovery.error && !discovery.baseExists && (
          <div style={{
            fontSize: 12,
            color: '#6d4c41',
            background: '#fff8e1',
            border: '1px solid #ffe082',
            borderRadius: 4,
            padding: '6px 10px',
          }}>
            REPOS_BASE_PATH (<code>{discovery.basePath}</code>) is not mounted.
          </div>
        )}

        {!discovery.loading && !discovery.error && discovery.baseExists && discovery.repos.length === 0 && (
          <div style={{ fontSize: 12, color: '#666', padding: '6px 8px' }}>
            No repositories found under <code>{discovery.basePath}</code>.
          </div>
        )}

        {!discovery.loading && discovery.repos.length > 0 && (
          <div style={{
            maxHeight: 200,
            overflowY: 'auto',
            border: '1px solid #e0e0e0',
            borderRadius: 4,
            background: '#fff',
          }}>
            <div style={{ padding: '4px 8px', fontSize: 11, color: '#666', borderBottom: '1px solid #eee' }}>
              Click to add to selected repositories
            </div>
            {discovery.repos.map((d) => {
              const added = isAlreadyAdded(d.path);
              return (
                <div
                  key={d.path}
                  onClick={() => !added && addDiscoveredRepo(d)}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 8,
                    padding: '6px 10px',
                    borderBottom: '1px solid #f0f0f0',
                    fontSize: 12,
                    background: added ? '#f5f5f5' : '#fff',
                    cursor: added ? 'default' : 'pointer',
                  }}
                  onMouseOver={(e) => { if (!added) e.currentTarget.style.background = '#e3f2fd'; }}
                  onMouseOut={(e) => { if (!added) e.currentTarget.style.background = added ? '#f5f5f5' : '#fff'; }}
                >
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontWeight: 500, color: added ? '#999' : '#222' }}>
                      {d.is_git && <span title="Git repository" style={{ marginRight: 4 }}>⌥</span>}
                      {d.name}
                    </div>
                    <div style={{ color: '#888', fontSize: 11, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {d.path}
                    </div>
                    {d.detected_languages?.length > 0 && (
                      <div style={{ marginTop: 2, display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                        {d.detected_languages.map((lang) => (
                          <span
                            key={lang}
                            style={{
                              fontSize: 10,
                              padding: '1px 6px',
                              borderRadius: 8,
                              background: lang === d.suggested_language ? '#e3f2fd' : '#f5f5f5',
                              color: lang === d.suggested_language ? '#1565c0' : '#666',
                              border: '1px solid ' + (lang === d.suggested_language ? '#90caf9' : '#e0e0e0'),
                            }}
                          >
                            {lang}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                  {added ? (
                    <span style={{ fontSize: 11, color: '#999', flexShrink: 0 }}>✓ added</span>
                  ) : (
                    <span style={{ fontSize: 11, color: '#1565c0', flexShrink: 0 }}>+ Add</span>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* ── Selected repositories ────────────────────────────────────────── */}
      {repos.length > 0 && (
        <div className="config-field">
          <label>Selected Repositories</label>
          <div style={{
            border: '1px solid #e0e0e0',
            borderRadius: 4,
            background: '#f9f9f9',
            maxHeight: 160,
            overflowY: 'auto',
          }}>
            {repos.map((r) => (
              <div
                key={r.path || r.name}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                  padding: '5px 10px',
                  borderBottom: '1px solid #eee',
                  fontSize: 12,
                }}
              >
                <div style={{ flex: 1, minWidth: 0 }}>
                  <span style={{ fontWeight: 500 }}>{r.name}</span>
                  {r.language && (
                    <span style={{
                      marginLeft: 6,
                      fontSize: 10,
                      padding: '1px 5px',
                      borderRadius: 8,
                      background: '#e3f2fd',
                      color: '#1565c0',
                      border: '1px solid #90caf9',
                    }}>
                      {r.language}
                    </span>
                  )}
                </div>
                <button
                  type="button"
                  onClick={() => removeRepo(r.path || r.name)}
                  style={{
                    background: '#ffebee',
                    border: '1px solid #ef9a9a',
                    borderRadius: 4,
                    cursor: 'pointer',
                    color: '#c62828',
                    padding: '2px 8px',
                    fontSize: 11,
                    flexShrink: 0,
                  }}
                  title="Remove from this analyzer"
                >
                  ✕
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      <small style={{ color: '#666', fontSize: 11, display: 'block', marginBottom: 8 }}>
        {repos.length} {repos.length === 1 ? 'repository' : 'repositories'} selected.
      </small>

      <div className="config-field checkbox-field">
        <label className="checkbox-label">
          <input
            type="checkbox"
            checked={config.autoIndex !== false}
            onChange={(e) => handleConfigChange('autoIndex', e.target.checked)}
          />
          <span>Auto-Index (index on first use and when stale)</span>
        </label>
      </div>

      {config.autoIndex !== false && (
        <div className="config-field">
          <label htmlFor="ca-stale-hours">Re-index after (hours)</label>
          <input
            id="ca-stale-hours"
            type="number"
            value={config.staleAfterHours || 24}
            onChange={(e) =>
              handleConfigChange('staleAfterHours', parseInt(e.target.value, 10))
            }
            min={1}
            style={{ width: 80 }}
          />
        </div>
      )}

      <div className="config-field checkbox-field">
        <label className="checkbox-label">
          <input
            type="checkbox"
            checked={config.preSummary || false}
            onChange={(e) => handleConfigChange('preSummary', e.target.checked)}
          />
          <span>Pre-Execution Summary</span>
        </label>
        <small style={{ color: '#666', fontSize: 11 }}>
          Runs a broad code search before the agent starts and injects a summary
          (minimal tokens).
        </small>
      </div>
    </>
  );
}

CodeAnalyzerConfig.propTypes = {
  config: PropTypes.object.isRequired,
  handleConfigChange: PropTypes.func.isRequired,
};

NodeConfigPanel.propTypes = {
  node: PropTypes.shape({
    id: PropTypes.string,
    type: PropTypes.string,
    data: PropTypes.object,
  }),
  workflowName: PropTypes.string,
  onUpdate: PropTypes.func.isRequired,
  onClose: PropTypes.func.isRequired,
};

NodeConfigPanel.defaultProps = {
  node: null,
  workflowName: '',
};

export default NodeConfigPanel;