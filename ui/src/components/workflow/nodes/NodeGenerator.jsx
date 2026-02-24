import React, { memo } from 'react';
import { Handle } from 'reactflow';
import { nodeConfigurations } from './nodeConfig';

const NodeGenerator = ({ data, isConnectable, type }) => {
  const config = nodeConfigurations[type];

  if (!config) {
    console.error(`Unknown node type: ${type}`);
    return null;
  }

  // Get dynamic icon (string or object with spinner)
  const icon = config.getIcon ? config.getIcon(data) : config.icon;
  const iconStr = typeof icon === 'object' ? icon.icon : icon;
  const showSpinner = data?.processing || data?.initializing;

  // Get status
  const status = config.getStatus?.(data);

  // Get extra content
  const extra = config.getExtra?.(data);

  return (
    <div className={config.className}>
      {/* Handles */}
      {config.handles.map((handle, i) => (
        <Handle
          key={`${handle.id}-${i}`}
          type={handle.type}
          position={handle.position}
          id={handle.id}
          isConnectable={isConnectable}
          style={handle.style}
        />
      ))}

      {/* Header */}
      <div className="node-header">
        <div className="node-icon">
          {showSpinner ? <div className="processing-spinner">{iconStr}</div> : iconStr}
        </div>
        <div className="node-title">{config.title}</div>
        {showSpinner && <div className="processing-indicator"><div className="spinner"></div></div>}
      </div>

      {/* Content */}
      <div className="node-content">
        <div className="node-label">{data?.label || config.defaultLabel}</div>
        <div className="node-description">
          {data?.description || data?.model || data?.type || data?.channel ||
            data?.toolType || data?.memoryType || config.defaultDescription}
        </div>

        {/* Extra info */}
        {extra && (
          <div className={`node-${extra.class}`} style={{ fontSize: '10px', color: '#666', marginTop: '2px' }}>
            {extra.text}
          </div>
        )}

        {/* Status */}
        {status && (
          <div className={`node-status ${status.class || ''}`}>
            {status.icon && <span className="status-icon">{status.icon}</span>}
            <span className="status-text">{status.text}</span>
            {status.model && <div className="model-info">Model: {status.model}</div>}
          </div>
        )}

        {/* Agent connection labels */}
        {type === 'agent' && (
          <div className="connection-labels">
            <div className="connection-label model-label">Chat Model</div>
            <div className="connection-label memory-label">Memory</div>
            <div className="connection-label tool-label">Tool</div>
          </div>
        )}
      </div>
    </div>
  );
};

export default memo(NodeGenerator);
