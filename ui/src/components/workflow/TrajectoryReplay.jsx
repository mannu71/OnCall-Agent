import React, { useState, useEffect, useRef } from 'react';
import PropTypes from 'prop-types';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Slider } from '@/components/ui/slider';
import { Badge } from '@/components/ui/badge';
import { Separator } from '@/components/ui/separator';
import {
    Play,
    Pause,
    SkipBack,
    SkipForward,
    FastForward,
    Rewind,
    Clock,
    Activity,
    Zap,
    Terminal
} from 'lucide-react';

/**
 * TrajectoryReplay - Playback UI for workflow execution trajectories
 * 
 * Features:
 * - Timeline scrubbing
 * - Play/pause controls
 * - Speed control (0.5x, 1x, 2x, 4x)
 * - Event filtering
 * - Node highlighting
 * - Token streaming replay
 * 
 * Props:
 * - executionId: Execution ID to replay
 * - events: Array of execution events
 * - onNodeHighlight: Callback when a node should be highlighted
 */
const TrajectoryReplay = ({ executionId, events = [], onNodeHighlight }) => {
    const [isPlaying, setIsPlaying] = useState(false);
    const [currentIndex, setCurrentIndex] = useState(0);
    const [playbackSpeed, setPlaybackSpeed] = useState(1);
    const [showFilters, setShowFilters] = useState(false);
    const [eventFilters, setEventFilters] = useState({
        workflow: true,
        node: true,
        agent: true,
        system: true
    });
    
    const playbackTimerRef = useRef(null);
    const startTimeRef = useRef(null);
    
    // Filter events based on active filters
    const filteredEvents = events.filter(event => {
        const type = event.event_type;
        
        if (type.startsWith('workflow_') && !eventFilters.workflow) return false;
        if (type.startsWith('node_') && !eventFilters.node) return false;
        if (type.startsWith('agent_') && !eventFilters.agent) return false;
        if (type === 'keepalive' || type === 'error') {
            if (!eventFilters.system) return false;
        }
        
        return true;
    });
    
    const currentEvent = filteredEvents[currentIndex];
    
    // Calculate execution duration
    const executionDuration = events.length > 0
        ? new Date(events[events.length - 1].timestamp) - new Date(events[0].timestamp)
        : 0;
    
    // Playback control
    useEffect(() => {
        if (isPlaying && currentIndex < filteredEvents.length - 1) {
            const baseDelay = 500; // Base delay between events (ms)
            const delay = baseDelay / playbackSpeed;
            
            playbackTimerRef.current = setTimeout(() => {
                setCurrentIndex(prev => prev + 1);
            }, delay);
        } else if (currentIndex >= filteredEvents.length - 1) {
            setIsPlaying(false);
        }
        
        return () => {
            if (playbackTimerRef.current) {
                clearTimeout(playbackTimerRef.current);
            }
        };
    }, [isPlaying, currentIndex, filteredEvents.length, playbackSpeed]);
    
    // Highlight current node
    useEffect(() => {
        if (currentEvent && onNodeHighlight) {
            const nodeId = currentEvent.data?.nodeId || currentEvent.data?.node_id;
            if (nodeId) {
                onNodeHighlight(nodeId);
            }
        }
    }, [currentEvent, onNodeHighlight]);
    
    const handlePlayPause = () => {
        setIsPlaying(!isPlaying);
    };
    
    const handleReset = () => {
        setIsPlaying(false);
        setCurrentIndex(0);
    };
    
    const handleStepForward = () => {
        if (currentIndex < filteredEvents.length - 1) {
            setCurrentIndex(prev => prev + 1);
        }
    };
    
    const handleStepBackward = () => {
        if (currentIndex > 0) {
            setCurrentIndex(prev => prev - 1);
        }
    };
    
    const handleSliderChange = (value) => {
        setCurrentIndex(value[0]);
        setIsPlaying(false);
    };
    
    const handleSpeedChange = () => {
        const speeds = [0.5, 1, 2, 4];
        const currentSpeedIndex = speeds.indexOf(playbackSpeed);
        const nextSpeedIndex = (currentSpeedIndex + 1) % speeds.length;
        setPlaybackSpeed(speeds[nextSpeedIndex]);
    };
    
    const toggleFilter = (filterKey) => {
        setEventFilters(prev => ({
            ...prev,
            [filterKey]: !prev[filterKey]
        }));
        setCurrentIndex(0); // Reset to start when filters change
    };
    
    const getEventIcon = (eventType) => {
        if (eventType.startsWith('workflow_')) return <Activity className="w-4 h-4" />;
        if (eventType.startsWith('node_')) return <Zap className="w-4 h-4" />;
        if (eventType.startsWith('agent_')) return <Terminal className="w-4 h-4" />;
        return <Clock className="w-4 h-4" />;
    };
    
    const getEventColor = (eventType) => {
        if (eventType.includes('failed') || eventType.includes('error')) return 'text-red-500';
        if (eventType.includes('completed') || eventType.includes('done')) return 'text-green-500';
        if (eventType.includes('started') || eventType.includes('start')) return 'text-blue-500';
        return 'text-gray-500';
    };
    
    const formatTimestamp = (timestamp) => {
        if (!timestamp) return '';
        const date = new Date(timestamp);
        return date.toLocaleTimeString('en-US', { 
            hour12: false, 
            hour: '2-digit', 
            minute: '2-digit', 
            second: '2-digit',
            fractionalSecondDigits: 3
        });
    };
    
    const formatDuration = (ms) => {
        if (ms < 1000) return `${ms}ms`;
        if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
        return `${Math.floor(ms / 60000)}m ${Math.floor((ms % 60000) / 1000)}s`;
    };
    
    if (!events || events.length === 0) {
        return (
            <Card>
                <CardContent className="p-6 text-center text-muted-foreground">
                    No execution events to replay
                </CardContent>
            </Card>
        );
    }
    
    return (
        <Card className="w-full">
            <CardHeader>
                <div className="flex items-center justify-between">
                    <CardTitle className="flex items-center gap-2">
                        <Activity className="w-5 h-5" />
                        Execution Replay
                    </CardTitle>
                    <div className="flex items-center gap-2">
                        <Badge variant="outline">
                            {filteredEvents.length} events
                        </Badge>
                        <Badge variant="outline">
                            {formatDuration(executionDuration)}
                        </Badge>
                    </div>
                </div>
            </CardHeader>
            
            <CardContent className="space-y-4">
                {/* Playback Controls */}
                <div className="flex items-center gap-2">
                    <Button
                        size="icon"
                        variant="outline"
                        onClick={handleReset}
                        title="Reset to start"
                    >
                        <SkipBack className="w-4 h-4" />
                    </Button>
                    
                    <Button
                        size="icon"
                        variant="outline"
                        onClick={handleStepBackward}
                        disabled={currentIndex === 0}
                        title="Step backward"
                    >
                        <Rewind className="w-4 h-4" />
                    </Button>
                    
                    <Button
                        size="icon"
                        variant="default"
                        onClick={handlePlayPause}
                        title={isPlaying ? 'Pause' : 'Play'}
                    >
                        {isPlaying ? (
                            <Pause className="w-4 h-4" />
                        ) : (
                            <Play className="w-4 h-4" />
                        )}
                    </Button>
                    
                    <Button
                        size="icon"
                        variant="outline"
                        onClick={handleStepForward}
                        disabled={currentIndex >= filteredEvents.length - 1}
                        title="Step forward"
                    >
                        <FastForward className="w-4 h-4" />
                    </Button>
                    
                    <Button
                        size="icon"
                        variant="outline"
                        onClick={() => setCurrentIndex(filteredEvents.length - 1)}
                        title="Skip to end"
                    >
                        <SkipForward className="w-4 h-4" />
                    </Button>
                    
                    <Separator orientation="vertical" className="h-8" />
                    
                    <Button
                        size="sm"
                        variant="outline"
                        onClick={handleSpeedChange}
                        title="Playback speed"
                    >
                        {playbackSpeed}x
                    </Button>
                    
                    <Separator orientation="vertical" className="h-8" />
                    
                    <Button
                        size="sm"
                        variant="outline"
                        onClick={() => setShowFilters(!showFilters)}
                    >
                        Filters
                    </Button>
                </div>
                
                {/* Event Filters */}
                {showFilters && (
                    <div className="flex gap-2 p-3 bg-muted/50 rounded-md">
                        <Button
                            size="sm"
                            variant={eventFilters.workflow ? 'default' : 'outline'}
                            onClick={() => toggleFilter('workflow')}
                        >
                            Workflow
                        </Button>
                        <Button
                            size="sm"
                            variant={eventFilters.node ? 'default' : 'outline'}
                            onClick={() => toggleFilter('node')}
                        >
                            Nodes
                        </Button>
                        <Button
                            size="sm"
                            variant={eventFilters.agent ? 'default' : 'outline'}
                            onClick={() => toggleFilter('agent')}
                        >
                            Agent
                        </Button>
                        <Button
                            size="sm"
                            variant={eventFilters.system ? 'default' : 'outline'}
                            onClick={() => toggleFilter('system')}
                        >
                            System
                        </Button>
                    </div>
                )}
                
                {/* Timeline Slider */}
                <div className="space-y-2">
                    <Slider
                        value={[currentIndex]}
                        onValueChange={handleSliderChange}
                        max={filteredEvents.length - 1}
                        step={1}
                        className="w-full"
                    />
                    <div className="flex justify-between text-xs text-muted-foreground">
                        <span>Event {currentIndex + 1} of {filteredEvents.length}</span>
                        <span>{currentEvent ? formatTimestamp(currentEvent.timestamp) : ''}</span>
                    </div>
                </div>
                
                <Separator />
                
                {/* Current Event Display */}
                {currentEvent && (
                    <div className="space-y-3">
                        <div className="flex items-center gap-2">
                            <div className={getEventColor(currentEvent.event_type)}>
                                {getEventIcon(currentEvent.event_type)}
                            </div>
                            <Badge variant="outline">
                                {currentEvent.event_type}
                            </Badge>
                            <span className="text-sm text-muted-foreground">
                                {formatTimestamp(currentEvent.timestamp)}
                            </span>
                        </div>
                        
                        {/* Event Data */}
                        <div className="rounded-md border bg-muted/30 p-3">
                            <pre className="text-xs overflow-x-auto">
                                {JSON.stringify(currentEvent.data, null, 2)}
                            </pre>
                        </div>
                        
                        {/* Special rendering for agent tokens */}
                        {currentEvent.event_type === 'agent_token' && currentEvent.data?.token && (
                            <div className="rounded-md border bg-[#1e1e1e] p-3">
                                <div className="text-xs text-[#858585] mb-1">Token Output:</div>
                                <div className="text-sm text-[#d4d4d4] font-mono">
                                    {currentEvent.data.token}
                                </div>
                            </div>
                        )}
                        
                        {/* Special rendering for tool calls */}
                        {currentEvent.event_type === 'agent_tool_call' && (
                            <div className="rounded-md border bg-blue-50 dark:bg-blue-950/20 p-3">
                                <div className="text-xs font-semibold text-blue-600 dark:text-blue-400 mb-1">
                                    Tool Call: {currentEvent.data?.tool}
                                </div>
                                <pre className="text-xs overflow-x-auto">
                                    {JSON.stringify(currentEvent.data?.args, null, 2)}
                                </pre>
                            </div>
                        )}
                        
                        {/* Special rendering for errors */}
                        {(currentEvent.event_type.includes('error') || currentEvent.event_type.includes('failed')) && (
                            <div className="rounded-md border border-red-200 bg-red-50 dark:bg-red-950/20 p-3">
                                <div className="text-xs font-semibold text-red-600 dark:text-red-400 mb-1">
                                    Error
                                </div>
                                <div className="text-sm text-red-700 dark:text-red-300">
                                    {currentEvent.data?.error || 'Unknown error'}
                                </div>
                            </div>
                        )}
                    </div>
                )}
            </CardContent>
        </Card>
    );
};

TrajectoryReplay.propTypes = {
    executionId: PropTypes.string.isRequired,
    events: PropTypes.arrayOf(PropTypes.shape({
        event_type: PropTypes.string.isRequired,
        timestamp: PropTypes.string.isRequired,
        data: PropTypes.object
    })).isRequired,
    onNodeHighlight: PropTypes.func
};

export default TrajectoryReplay;
