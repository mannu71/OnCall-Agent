import React, { useState } from 'react';
import PropTypes from 'prop-types';
import { ChevronDown, ChevronUp, Info } from 'lucide-react';

/**
 * Advanced configuration panel for Agent nodes
 * Includes: Skills and Advanced Features
 */
const AgentAdvancedConfig = ({ config, onConfigChange }) => {
    const [expandedSections, setExpandedSections] = useState({
        skills: false,
        features: false
    });

    const toggleSection = (section) => {
        setExpandedSections(prev => ({
            ...prev,
            [section]: !prev[section]
        }));
    };

    const handleSkillsChange = (field, value) => {
        onConfigChange('skills', {
            ...(config.skills || {}),
            [field]: value
        });
    };

    const handleFeaturesChange = (feature, field, value) => {
        onConfigChange('features', {
            ...(config.features || {}),
            [feature]: {
                ...(config.features?.[feature] || {}),
                [field]: value
            }
        });
    };

    const CollapsibleSection = ({ title, expanded, onToggle, enabled, onEnabledChange, children, helpText }) => (
        <div style={{
            border: '1px solid #e0e0e0',
            borderRadius: '6px',
            marginBottom: '12px',
            overflow: 'hidden'
        }}>
            <div
                onClick={onToggle}
                style={{
                    padding: '12px 16px',
                    background: expanded ? '#f5f5f5' : '#fafafa',
                    cursor: 'pointer',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    borderBottom: expanded ? '1px solid #e0e0e0' : 'none'
                }}
            >
                <div style={{ display: 'flex', alignItems: 'center', gap: '12px', flex: 1 }}>
                    <span style={{ fontSize: '14px', fontWeight: '600', color: '#333' }}>{title}</span>
                    {helpText && (
                        <div style={{ position: 'relative', display: 'inline-block' }} title={helpText}>
                            <Info size={14} color="#666" />
                        </div>
                    )}
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                    {onEnabledChange && (
                        <label
                            onClick={(e) => e.stopPropagation()}
                            style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '12px', color: '#666' }}
                        >
                            <input
                                type="checkbox"
                                checked={enabled || false}
                                onChange={(e) => onEnabledChange(e.target.checked)}
                            />
                            Enabled
                        </label>
                    )}
                    {expanded ? <ChevronUp size={18} /> : <ChevronDown size={18} />}
                </div>
            </div>
            {expanded && (
                <div style={{ padding: '16px' }}>
                    {children}
                </div>
            )}
        </div>
    );

    return (
        <div style={{ marginTop: '16px' }}>
            <h4 style={{ fontSize: '13px', fontWeight: '600', color: '#666', marginBottom: '12px', textTransform: 'uppercase' }}>
                Advanced Features
            </h4>

            {/* Skills Configuration */}
            <CollapsibleSection
                title="Skills Framework"
                expanded={expandedSections.skills}
                onToggle={() => toggleSection('skills')}
                helpText="Preload reusable workflow skills (runbooks)"
            >
                <div className="config-field">
                    <label>Preloaded Skills</label>
                    <div style={{ maxHeight: '120px', overflowY: 'auto', marginBottom: '8px' }}>
                        {(config.skills?.preloaded || []).map((skill, index) => (
                            <div key={index} style={{ display: 'flex', gap: '5px', marginBottom: '5px' }}>
                                <input
                                    type="text"
                                    value={skill}
                                    onChange={(e) => {
                                        const newSkills = [...(config.skills?.preloaded || [])];
                                        newSkills[index] = e.target.value;
                                        handleSkillsChange('preloaded', newSkills);
                                    }}
                                    placeholder="incident-response"
                                    style={{ flex: 1 }}
                                />
                                <button
                                    onClick={() => {
                                        const newSkills = (config.skills?.preloaded || []).filter((_, i) => i !== index);
                                        handleSkillsChange('preloaded', newSkills);
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
                        onClick={() => handleSkillsChange('preloaded', [...(config.skills?.preloaded || []), ''])}
                        style={{
                            background: '#4caf50',
                            color: 'white',
                            border: 'none',
                            borderRadius: '4px',
                            cursor: 'pointer',
                            padding: '6px 12px',
                            width: '100%',
                            fontSize: '12px'
                        }}
                    >+ Add Skill</button>
                    <small style={{ color: '#666', fontSize: '11px', display: 'block', marginTop: '8px' }}>
                        Skills from data/skills/ directory (e.g., incident-response, aws-troubleshooting)
                    </small>
                </div>
            </CollapsibleSection>

            {/* Advanced Features */}
            <CollapsibleSection
                title="Advanced Features"
                expanded={expandedSections.features}
                onToggle={() => toggleSection('features')}
                helpText="Context compression, prompt caching, trajectory storage, rate limiting"
            >
                {/* Context Compression */}
                <div style={{ marginBottom: '16px', paddingBottom: '16px', borderBottom: '1px solid #e0e0e0' }}>
                    <label style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '12px' }}>
                        <input
                            type="checkbox"
                            checked={config.features?.context_compression?.enabled || false}
                            onChange={(e) => handleFeaturesChange('context_compression', 'enabled', e.target.checked)}
                        />
                        <span style={{ fontWeight: '600', fontSize: '13px' }}>Context Compression</span>
                    </label>
                    {config.features?.context_compression?.enabled && (
                        <>
                            <div className="config-field">
                                <label htmlFor="compression-threshold">Threshold (%)</label>
                                <input
                                    id="compression-threshold"
                                    type="number"
                                    min="50"
                                    max="95"
                                    step="5"
                                    value={(config.features?.context_compression?.threshold_percent || 0.75) * 100}
                                    onChange={(e) => handleFeaturesChange('context_compression', 'threshold_percent', parseFloat(e.target.value) / 100)}
                                />
                                <small style={{ color: '#666', fontSize: '11px' }}>
                                    Compress when context reaches this % of limit
                                </small>
                            </div>
                            <div className="config-field">
                                <label htmlFor="compression-protect">Protect First N Turns</label>
                                <input
                                    id="compression-protect"
                                    type="number"
                                    min="1"
                                    max="10"
                                    value={config.features?.context_compression?.protect_first_n || 3}
                                    onChange={(e) => handleFeaturesChange('context_compression', 'protect_first_n', parseInt(e.target.value))}
                                />
                            </div>
                        </>
                    )}
                </div>

                {/* Prompt Caching */}
                <div style={{ marginBottom: '16px', paddingBottom: '16px', borderBottom: '1px solid #e0e0e0' }}>
                    <label style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '12px' }}>
                        <input
                            type="checkbox"
                            checked={config.features?.prompt_caching?.enabled || false}
                            onChange={(e) => handleFeaturesChange('prompt_caching', 'enabled', e.target.checked)}
                        />
                        <span style={{ fontWeight: '600', fontSize: '13px' }}>Prompt Caching</span>
                    </label>
                    {config.features?.prompt_caching?.enabled && (
                        <div className="config-field">
                            <label htmlFor="cache-ttl">Cache TTL</label>
                            <input
                                id="cache-ttl"
                                type="text"
                                value={config.features?.prompt_caching?.ttl || '5m'}
                                onChange={(e) => handleFeaturesChange('prompt_caching', 'ttl', e.target.value)}
                                placeholder="5m"
                            />
                            <small style={{ color: '#666', fontSize: '11px' }}>
                                Time to live (e.g., 5m, 1h)
                            </small>
                        </div>
                    )}
                </div>

                {/* Trajectory Storage */}
                <div style={{ marginBottom: '16px', paddingBottom: '16px', borderBottom: '1px solid #e0e0e0' }}>
                    <label style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '12px' }}>
                        <input
                            type="checkbox"
                            checked={config.features?.trajectory_storage?.enabled || false}
                            onChange={(e) => handleFeaturesChange('trajectory_storage', 'enabled', e.target.checked)}
                        />
                        <span style={{ fontWeight: '600', fontSize: '13px' }}>Trajectory Storage</span>
                    </label>
                    {config.features?.trajectory_storage?.enabled && (
                        <label style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '12px' }}>
                            <input
                                type="checkbox"
                                checked={config.features?.trajectory_storage?.persist_turns || false}
                                onChange={(e) => handleFeaturesChange('trajectory_storage', 'persist_turns', e.target.checked)}
                            />
                            Persist all turns
                        </label>
                    )}
                </div>

                {/* Rate Limit Tracking */}
                <div>
                    <label style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '12px' }}>
                        <input
                            type="checkbox"
                            checked={config.features?.rate_limit_tracking?.enabled || false}
                            onChange={(e) => handleFeaturesChange('rate_limit_tracking', 'enabled', e.target.checked)}
                        />
                        <span style={{ fontWeight: '600', fontSize: '13px' }}>Rate Limit Tracking</span>
                    </label>
                    {config.features?.rate_limit_tracking?.enabled && (
                        <div className="config-field">
                            <label htmlFor="rate-threshold">Warning Threshold (%)</label>
                            <input
                                id="rate-threshold"
                                type="number"
                                min="50"
                                max="95"
                                step="5"
                                value={(config.features?.rate_limit_tracking?.warning_threshold || 0.80) * 100}
                                onChange={(e) => handleFeaturesChange('rate_limit_tracking', 'warning_threshold', parseFloat(e.target.value) / 100)}
                            />
                            <small style={{ color: '#666', fontSize: '11px' }}>
                                Warn when rate limit reaches this %
                            </small>
                        </div>
                    )}
                </div>
            </CollapsibleSection>
        </div>
    );
};

AgentAdvancedConfig.propTypes = {
    config: PropTypes.object.isRequired,
    onConfigChange: PropTypes.func.isRequired
};

export default AgentAdvancedConfig;
