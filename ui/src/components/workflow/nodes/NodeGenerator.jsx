import React, { memo } from 'react';
import { Handle } from 'reactflow';
import { nodeConfigurations } from './nodeConfig';

const NodeGenerator = ({ data, isConnectable, type }) => {
  const config = nodeConfigurations[type];
  
  if (!config) {
    console.error(`Unknown node type: ${type}`);
    return null;
  }

  // Get dynamic icon
  const iconData = config.getIcon ? config.getIcon(data) : { icon: config.icon };
  const isProcessing = data?.processing || false;
  const isInitializing = data?.initializing || false;

  // Render handles
  const renderHandles = () => {
    return config.handles.map((handle, index) => (
      <Handle
        key={`${handle.id}-${index}`}
        type={handle.type}
        position={handle.position}
        id={handle.id}
        isConnectable={isConnectable}
        style={handle.style}
      />
    ));
  };

  // Render node header
  const renderHeader = () => (
    <div className="node-header">
      <div className="node-icon">
        {iconData.spinner ? (
          <div className={isInitializing ? "initializing-spinner" : "processing-spinner"}>
            {iconData.icon}
          </div>
        ) : (
          iconData.icon
        )}
      </div>
      <div className="node-title">{config.title}</div>
      {(isProcessing || isInitializing) && (
        <div className="processing-indicator">
          <div className="spinner"></div>
        </div>
      )}
    </div>
  );

  // Render status section
  const renderStatus = () => {
    if (!config.renderStatus) {
      return <div className="node-status">{data?.status || 'Ready'}</div>;
    }

    const statusData = config.renderStatus(data);
    return (
      <div className={`node-status ${statusData.className || ''}`}>
        {statusData.icon && <div className="status-icon">{statusData.icon}</div>}
        <span className="status-text">{statusData.text}</span>
        {statusData.modelInfo && (
          <div className="model-info">Model: {statusData.modelInfo}</div>
        )}
      </div>
    );
  };

  // Render processing section
  const renderProcessing = () => {
    if (!config.renderProcessing) return null;
    
    const processingData = config.renderProcessing(data);
    if (!processingData) return null;

    return (
      <div className="processing-status">
        <span className="processing-text">{processingData.text}</span>
        {processingData.message && (
          <div className="last-message">"{processingData.message}"</div>
        )}
      </div>
    );
  };

  // Render extra content
  const renderExtra = () => {
    if (!config.renderExtra) return null;
    
    const extraData = config.renderExtra(data);
    if (!extraData) return null;

    return (
      <div className={extraData.className} style={{
        fontSize: '10px',
        color: '#666',
        marginTop: '2px',
        textTransform: 'uppercase'
      }}>
        {extraData.text}
      </div>
    );
  };

  // Render connection labels for agent nodes
  const renderConnectionLabels = () => {
    if (type !== 'agent') return null;
    
    return (
      <div className="connection-labels">
        <div className="connection-label model-label">Chat Model</div>
        <div className="connection-label memory-label">Memory</div>
        <div className="connection-label tool-label">Tool</div>
      </div>
    );
  };

  return (
    <div className={config.className}>
      {renderHandles()}
      {renderHeader()}
      <div className="node-content">
        <div className="node-label">{data?.label || config.defaultLabel}</div>
        <div className="node-description">
          {data?.description || data?.model || data?.type || data?.channel || 
           data?.toolType || data?.memoryType || config.defaultDescription}
        </div>
        {renderExtra()}
        {renderStatus()}
        {renderProcessing()}
        {renderConnectionLabels()}
      </div>
    </div>
  );
};

export default memo(NodeGenerator);
