import React, { useState, useEffect } from 'react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Badge } from '@/components/ui/badge';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { 
    Plus, 
    Trash2, 
    Play, 
    RefreshCw, 
    Search
} from 'lucide-react';

const API_BASE_URL = 'http://localhost:8000/api/v1';

// Severity Badge Component
// eslint-disable-next-line react/prop-types
function SeverityBadge({ severity }) {
    const configs = {
        critical: 'bg-red-100 text-red-800 hover:bg-red-100',
        high: 'bg-orange-100 text-orange-800 hover:bg-orange-100',
        medium: 'bg-yellow-100 text-yellow-800 hover:bg-yellow-100',
        low: 'bg-blue-100 text-blue-800 hover:bg-blue-100',
        info: 'bg-green-100 text-green-800 hover:bg-green-100'
    };

    return (
        <Badge variant="secondary" className={configs[severity] || configs.info}>
            {severity?.toUpperCase() || 'INFO'}
        </Badge>
    );
}

// Status Badge Component
// eslint-disable-next-line react/prop-types
function StatusBadge({ status }) {
    const configs = {
        new: 'bg-red-100 text-red-800 hover:bg-red-100',
        acknowledged: 'bg-orange-100 text-orange-800 hover:bg-orange-100',
        resolved: 'bg-green-100 text-green-800 hover:bg-green-100',
        dismissed: 'bg-gray-100 text-gray-800 hover:bg-gray-100'
    };

    return (
        <Badge variant="secondary" className={configs[status] || configs.new}>
            {status?.toUpperCase() || 'NEW'}
        </Badge>
    );
}

// Log Watch Configuration Component
export default function LogWatchConfig() {
    const [tabValue, setTabValue] = useState('alerts');
    const [alerts, setAlerts] = useState([]);
    const [alertSummary, setAlertSummary] = useState(null);
    const [knownIssues, setKnownIssues] = useState([]);
    const [patterns, setPatterns] = useState([]);
    const [baselines, setBaselines] = useState([]);
    const [loading, setLoading] = useState(false);

    // Dialog states
    const [watchDialogOpen, setWatchDialogOpen] = useState(false);
    const [alertDialogOpen, setAlertDialogOpen] = useState(false);
    const [patternDialogOpen, setPatternDialogOpen] = useState(false);
    const [baselineDialogOpen, setBaselineDialogOpen] = useState(false);
    const [issueDialogOpen, setIssueDialogOpen] = useState(false);

    // Form states
    const [watchForm, setWatchForm] = useState({
        log_group_names: [''],
        time_range_minutes: 60,
        filter_pattern: '',
        region: 'us-east-1'
    });

    const [alertForm, setAlertForm] = useState({
        log_group: '',
        alert_type: 'anomaly',
        severity: 'medium',
        message: ''
    });

    const [patternForm, setPatternForm] = useState({
        name: '',
        pattern: '',
        pattern_type: 'error',
        severity: 3,
        description: ''
    });

    const [baselineForm, setBaselineForm] = useState({
        metric_name: '',
        log_group: '',
        normal_range_min: 0,
        normal_range_max: 100,
        threshold_warning: 80,
        threshold_critical: 90,
        time_window: '5m'
    });

    const [issueForm, setIssueForm] = useState({
        title: '',
        description: '',
        symptoms: [''],
        solution: '',
        category: 'general'
    });

    // Watch results
    const [watchResults, setWatchResults] = useState(null);
    const [anomalyResults, setAnomalyResults] = useState(null);

    // Fetch data on mount
    useEffect(() => {
        fetchAlerts();
        fetchAlertSummary();
        fetchKnownIssues();
        fetchPatterns();
        fetchBaselines();
    }, []);

    // API Functions
    const fetchAlerts = async () => {
        try {
            const response = await fetch(`${API_BASE_URL}/log-watch/alerts`);
            const data = await response.json();
            if (data.success) {
                setAlerts(data.alerts);
            }
        } catch (error) {
            console.error('Error fetching alerts:', error);
        }
    };

    const fetchAlertSummary = async () => {
        try {
            const response = await fetch(`${API_BASE_URL}/log-watch/alerts/summary`);
            const data = await response.json();
            if (data.success) {
                setAlertSummary(data.summary);
            }
        } catch (error) {
            console.error('Error fetching alert summary:', error);
        }
    };

    const fetchKnownIssues = async () => {
        try {
            const response = await fetch(`${API_BASE_URL}/log-watch/known-issues`);
            const data = await response.json();
            if (data.success) {
                setKnownIssues(data.issues);
            }
        } catch (error) {
            console.error('Error fetching known issues:', error);
        }
    };

    const fetchPatterns = async () => {
        try {
            const response = await fetch(`${API_BASE_URL}/log-watch/patterns`);
            const data = await response.json();
            setPatterns(data);
        } catch (error) {
            console.error('Error fetching patterns:', error);
        }
    };

    const fetchBaselines = async () => {
        try {
            const response = await fetch(`${API_BASE_URL}/log-watch/baselines`);
            const data = await response.json();
            setBaselines(data);
        } catch (error) {
            console.error('Error fetching baselines:', error);
        }
    };

    // Watch Logs
    const handleWatchLogs = async () => {
        setLoading(true);
        try {
            const response = await fetch(`${API_BASE_URL}/log-watch/watch`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    ...watchForm,
                    log_group_names: watchForm.log_group_names.filter(g => g.trim())
                })
            });
            const data = await response.json();
            setWatchResults(data);
        } catch (error) {
            console.error('Error watching logs:', error);
        }
        setLoading(false);
        setWatchDialogOpen(false);
    };

    // Detect Anomalies
    const handleDetectAnomalies = async () => {
        setLoading(true);
        try {
            const response = await fetch(`${API_BASE_URL}/log-watch/detect-anomalies`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    log_group_names: watchForm.log_group_names.filter(g => g.trim()),
                    time_range_minutes: watchForm.time_range_minutes,
                    sensitivity: 'medium'
                })
            });
            const data = await response.json();
            setAnomalyResults(data);
        } catch (error) {
            console.error('Error detecting anomalies:', error);
        }
        setLoading(false);
    };

    // Create Alert
    const handleCreateAlert = async () => {
        try {
            const response = await fetch(`${API_BASE_URL}/log-watch/alerts`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(alertForm)
            });
            const data = await response.json();
            if (data.success) {
                fetchAlerts();
                fetchAlertSummary();
            }
        } catch (error) {
            console.error('Error creating alert:', error);
        }
        setAlertDialogOpen(false);
    };

    // Acknowledge Alert
    const handleAcknowledgeAlert = async (alertId) => {
        try {
            const response = await fetch(`${API_BASE_URL}/log-watch/alerts/${alertId}/acknowledge`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ acknowledged_by: 'ui-user' })
            });
            const data = await response.json();
            if (data.success) {
                fetchAlerts();
            }
        } catch (error) {
            console.error('Error acknowledging alert:', error);
        }
    };

    // Resolve Alert
    const handleResolveAlert = async (alertId) => {
        try {
            const response = await fetch(`${API_BASE_URL}/log-watch/alerts/${alertId}/resolve`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ resolved_by: 'ui-user', resolution: 'Resolved via UI' })
            });
            const data = await response.json();
            if (data.success) {
                fetchAlerts();
                fetchAlertSummary();
            }
        } catch (error) {
            console.error('Error resolving alert:', error);
        }
    };

    // Add Pattern
    const handleAddPattern = async () => {
        try {
            const response = await fetch(`${API_BASE_URL}/log-watch/patterns`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(patternForm)
            });
            const data = await response.json();
            if (data.id) {
                fetchPatterns();
            }
        } catch (error) {
            console.error('Error adding pattern:', error);
        }
        setPatternDialogOpen(false);
    };

    // Add Baseline
    const handleAddBaseline = async () => {
        try {
            const response = await fetch(`${API_BASE_URL}/log-watch/baselines`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    metric_name: baselineForm.metric_name,
                    log_group: baselineForm.log_group,
                    normal_range: {
                        min: baselineForm.normal_range_min,
                        max: baselineForm.normal_range_max
                    },
                    thresholds: {
                        warning: baselineForm.threshold_warning,
                        critical: baselineForm.threshold_critical
                    },
                    time_window: baselineForm.time_window
                })
            });
            const data = await response.json();
            if (data.id) {
                fetchBaselines();
            }
        } catch (error) {
            console.error('Error setting baseline:', error);
        }
        setBaselineDialogOpen(false);
    };

    // Add Known Issue
    const handleAddKnownIssue = async () => {
        try {
            const response = await fetch(`${API_BASE_URL}/log-watch/known-issues`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    ...issueForm,
                    symptoms: issueForm.symptoms.filter(s => s.trim())
                })
            });
            const data = await response.json();
            if (data.success) {
                fetchKnownIssues();
            }
        } catch (error) {
            console.error('Error adding known issue:', error);
        }
        setIssueDialogOpen(false);
    };

    return (
        <div className="p-6 space-y-6">
            {/* Header */}
            <div>
                <h1 className="text-3xl font-bold tracking-tight">CloudWatch Log Watch Analyzer</h1>
                <p className="text-muted-foreground mt-1">Monitor and analyze CloudWatch logs with intelligent alerting</p>
            </div>

            {/* Alert Summary Cards */}
            {alertSummary && (
                <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-6 gap-4">
                    <Card>
                        <CardHeader className="pb-2">
                            <CardTitle className="text-sm font-medium text-muted-foreground">Total Alerts</CardTitle>
                        </CardHeader>
                        <CardContent>
                            <div className="text-2xl font-bold">{alertSummary.total_alerts}</div>
                        </CardContent>
                    </Card>

                    <Card className="bg-red-50 border-red-200">
                        <CardHeader className="pb-2">
                            <CardTitle className="text-sm font-medium text-red-700">Critical</CardTitle>
                        </CardHeader>
                        <CardContent>
                            <div className="text-2xl font-bold text-red-700">{alertSummary.by_severity?.critical || 0}</div>
                        </CardContent>
                    </Card>

                    <Card className="bg-orange-50 border-orange-200">
                        <CardHeader className="pb-2">
                            <CardTitle className="text-sm font-medium text-orange-700">High</CardTitle>
                        </CardHeader>
                        <CardContent>
                            <div className="text-2xl font-bold text-orange-700">{alertSummary.by_severity?.high || 0}</div>
                        </CardContent>
                    </Card>

                    <Card className="bg-blue-50 border-blue-200">
                        <CardHeader className="pb-2">
                            <CardTitle className="text-sm font-medium text-blue-700">New</CardTitle>
                        </CardHeader>
                        <CardContent>
                            <div className="text-2xl font-bold text-blue-700">{alertSummary.by_status?.new || 0}</div>
                        </CardContent>
                    </Card>

                    <Card className="bg-yellow-50 border-yellow-200">
                        <CardHeader className="pb-2">
                            <CardTitle className="text-sm font-medium text-yellow-700">Acknowledged</CardTitle>
                        </CardHeader>
                        <CardContent>
                            <div className="text-2xl font-bold text-yellow-700">{alertSummary.by_status?.acknowledged || 0}</div>
                        </CardContent>
                    </Card>

                    <Card className="bg-green-50 border-green-200">
                        <CardHeader className="pb-2">
                            <CardTitle className="text-sm font-medium text-green-700">Resolved</CardTitle>
                        </CardHeader>
                        <CardContent>
                            <div className="text-2xl font-bold text-green-700">{alertSummary.by_status?.resolved || 0}</div>
                        </CardContent>
                    </Card>
                </div>
            )}

            {/* Tabs */}
            <Card>
                <div className="border-b">
                    <div className="flex space-x-1 p-1">
                        {['alerts', 'watch', 'patterns', 'baselines', 'issues'].map((tab) => (
                            <button
                                key={tab}
                                onClick={() => setTabValue(tab)}
                                className={`px-4 py-2 text-sm font-medium rounded transition-colors ${
                                    tabValue === tab
                                        ? 'bg-slate-100 text-slate-900'
                                        : 'text-slate-600 hover:text-slate-900 hover:bg-slate-50'
                                }`}
                            >
                                {tab === 'alerts' && 'Alerts'}
                                {tab === 'watch' && 'Watch Logs'}
                                {tab === 'patterns' && 'Patterns'}
                                {tab === 'baselines' && 'Baselines'}
                                {tab === 'issues' && 'Known Issues'}
                            </button>
                        ))}
                    </div>
                </div>

                {loading && (
                    <div className="h-1 bg-blue-500 animate-pulse"></div>
                )}

                {/* Alerts Tab */}
                {tabValue === 'alerts' && (
                    <CardContent className="pt-6">
                        <div className="flex gap-2 mb-4">
                            <Button onClick={() => setAlertDialogOpen(true)}>
                                <Plus className="w-4 h-4 mr-2" />
                                Create Alert
                            </Button>
                            <Button variant="outline" onClick={fetchAlerts}>
                                <RefreshCw className="w-4 h-4 mr-2" />
                                Refresh
                            </Button>
                        </div>

                        <div className="border rounded-lg">
                            <Table>
                                <TableHeader>
                                    <TableRow>
                                        <TableHead>ID</TableHead>
                                        <TableHead>Log Group</TableHead>
                                        <TableHead>Type</TableHead>
                                        <TableHead>Severity</TableHead>
                                        <TableHead>Status</TableHead>
                                        <TableHead>Message</TableHead>
                                        <TableHead>Created</TableHead>
                                        <TableHead>Actions</TableHead>
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {alerts.length === 0 ? (
                                        <TableRow>
                                            <TableCell colSpan={8} className="text-center text-muted-foreground py-8">
                                                No alerts found
                                            </TableCell>
                                        </TableRow>
                                    ) : (
                                        alerts.map((alert) => (
                                            <TableRow key={alert.id}>
                                                <TableCell className="font-mono text-xs">{alert.id}</TableCell>
                                                <TableCell>{alert.log_group}</TableCell>
                                                <TableCell>{alert.alert_type}</TableCell>
                                                <TableCell><SeverityBadge severity={alert.severity} /></TableCell>
                                                <TableCell><StatusBadge status={alert.status} /></TableCell>
                                                <TableCell className="max-w-[300px] truncate">{alert.message}</TableCell>
                                                <TableCell className="text-sm text-muted-foreground">
                                                    {alert.created_at ? new Date(alert.created_at).toLocaleString() : '-'}
                                                </TableCell>
                                                <TableCell>
                                                    <div className="flex gap-1">
                                                        {alert.status === 'new' && (
                                                            <Button size="sm" variant="outline" onClick={() => handleAcknowledgeAlert(alert.id)}>
                                                                Ack
                                                            </Button>
                                                        )}
                                                        {alert.status !== 'resolved' && alert.status !== 'dismissed' && (
                                                            <Button size="sm" variant="outline" onClick={() => handleResolveAlert(alert.id)}>
                                                                Resolve
                                                            </Button>
                                                        )}
                                                    </div>
                                                </TableCell>
                                            </TableRow>
                                        ))
                                    )}
                                </TableBody>
                            </Table>
                        </div>
                    </CardContent>
                )}

                {/* Watch Logs Tab */}
                {tabValue === 'watch' && (
                    <CardContent className="pt-6">
                        <div className="flex gap-2 mb-4">
                            <Button onClick={() => setWatchDialogOpen(true)}>
                                <Play className="w-4 h-4 mr-2" />
                                Watch Log Groups
                            </Button>
                            <Button variant="outline" onClick={handleDetectAnomalies}>
                                <Search className="w-4 h-4 mr-2" />
                                Detect Anomalies
                            </Button>
                        </div>

                        {watchResults && (
                            <Card>
                                <CardHeader>
                                    <CardTitle>Watch Results</CardTitle>
                                </CardHeader>
                                <CardContent>
                                    <pre className="bg-slate-50 p-4 rounded-lg overflow-auto text-xs">
                                        {JSON.stringify(watchResults, null, 2)}
                                    </pre>
                                </CardContent>
                            </Card>
                        )}

                        {anomalyResults && (
                            <Card className="mt-4">
                                <CardHeader>
                                    <CardTitle>Anomaly Detection Results</CardTitle>
                                </CardHeader>
                                <CardContent>
                                    <pre className="bg-slate-50 p-4 rounded-lg overflow-auto text-xs">
                                        {JSON.stringify(anomalyResults, null, 2)}
                                    </pre>
                                </CardContent>
                            </Card>
                        )}
                    </CardContent>
                )}

                {/* Patterns Tab */}
                {tabValue === 'patterns' && (
                    <CardContent className="pt-6">
                        <div className="flex gap-2 mb-4">
                            <Button onClick={() => setPatternDialogOpen(true)}>
                                <Plus className="w-4 h-4 mr-2" />
                                Add Pattern
                            </Button>
                        </div>

                        <div className="border rounded-lg">
                            <Table>
                                <TableHeader>
                                    <TableRow>
                                        <TableHead>Name</TableHead>
                                        <TableHead>Pattern</TableHead>
                                        <TableHead>Type</TableHead>
                                        <TableHead>Severity</TableHead>
                                        <TableHead>Description</TableHead>
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {patterns.length === 0 ? (
                                        <TableRow>
                                            <TableCell colSpan={5} className="text-center text-muted-foreground py-8">
                                                No patterns defined
                                            </TableCell>
                                        </TableRow>
                                    ) : (
                                        patterns.map((pattern) => (
                                            <TableRow key={pattern.id}>
                                                <TableCell className="font-medium">{pattern.name}</TableCell>
                                                <TableCell className="font-mono text-xs">{pattern.pattern}</TableCell>
                                                <TableCell>{pattern.pattern_type}</TableCell>
                                                <TableCell>{pattern.severity}</TableCell>
                                                <TableCell>{pattern.description}</TableCell>
                                            </TableRow>
                                        ))
                                    )}
                                </TableBody>
                            </Table>
                        </div>
                    </CardContent>
                )}

                {/* Baselines Tab */}
                {tabValue === 'baselines' && (
                    <CardContent className="pt-6">
                        <div className="flex gap-2 mb-4">
                            <Button onClick={() => setBaselineDialogOpen(true)}>
                                <Plus className="w-4 h-4 mr-2" />
                                Add Baseline
                            </Button>
                        </div>

                        <div className="border rounded-lg">
                            <Table>
                                <TableHeader>
                                    <TableRow>
                                        <TableHead>Metric</TableHead>
                                        <TableHead>Log Group</TableHead>
                                        <TableHead>Normal Range</TableHead>
                                        <TableHead>Warning</TableHead>
                                        <TableHead>Critical</TableHead>
                                        <TableHead>Window</TableHead>
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {baselines.length === 0 ? (
                                        <TableRow>
                                            <TableCell colSpan={6} className="text-center text-muted-foreground py-8">
                                                No baselines configured
                                            </TableCell>
                                        </TableRow>
                                    ) : (
                                        baselines.map((baseline) => (
                                            <TableRow key={baseline.id}>
                                                <TableCell className="font-medium">{baseline.metric_name}</TableCell>
                                                <TableCell>{baseline.log_group}</TableCell>
                                                <TableCell>
                                                    {baseline.normal_range?.min} - {baseline.normal_range?.max}
                                                </TableCell>
                                                <TableCell>{baseline.thresholds?.warning}</TableCell>
                                                <TableCell>{baseline.thresholds?.critical}</TableCell>
                                                <TableCell>{baseline.time_window}</TableCell>
                                            </TableRow>
                                        ))
                                    )}
                                </TableBody>
                            </Table>
                        </div>
                    </CardContent>
                )}

                {/* Known Issues Tab */}
                {tabValue === 'issues' && (
                    <CardContent className="pt-6">
                        <div className="flex gap-2 mb-4">
                            <Button onClick={() => setIssueDialogOpen(true)}>
                                <Plus className="w-4 h-4 mr-2" />
                                Add Known Issue
                            </Button>
                        </div>

                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                            {knownIssues.length === 0 ? (
                                <div className="col-span-2 text-center text-muted-foreground py-8">
                                    No known issues documented
                                </div>
                            ) : (
                                knownIssues.map((issue) => (
                                    <Card key={issue.id} className="border">
                                        <CardHeader>
                                            <CardTitle className="text-lg">{issue.title}</CardTitle>
                                            <Badge className="w-fit mt-2">{issue.category}</Badge>
                                        </CardHeader>
                                        <CardContent className="space-y-3">
                                            <div>
                                                <p className="text-sm text-muted-foreground">{issue.description}</p>
                                            </div>
                                            <div>
                                                <p className="text-sm font-semibold">Symptoms:</p>
                                                <p className="text-sm text-muted-foreground">{issue.symptoms?.join(', ')}</p>
                                            </div>
                                            <div>
                                                <p className="text-sm font-semibold">Solution:</p>
                                                <p className="text-sm text-muted-foreground">{issue.solution}</p>
                                            </div>
                                        </CardContent>
                                    </Card>
                                ))
                            )}
                        </div>
                    </CardContent>
                )}
            </Card>

            {/* Watch Dialog */}
            <Dialog open={watchDialogOpen} onOpenChange={setWatchDialogOpen}>
                <DialogContent className="max-w-2xl">
                    <DialogHeader>
                        <DialogTitle>Watch Log Groups</DialogTitle>
                        <DialogDescription>Configure CloudWatch log groups to watch</DialogDescription>
                    </DialogHeader>
                    
                    <div className="space-y-4 py-4">
                        <div>
                            <label htmlFor="log-group-names" className="text-sm font-medium mb-2 block">Log Group Names</label>
                            {watchForm.log_group_names.map((name, index) => (
                                <div key={`log-group-${index}`} className="flex gap-2 mb-2">
                                    <Input
                                        value={name}
                                        onChange={(e) => {
                                            const newNames = [...watchForm.log_group_names];
                                            newNames[index] = e.target.value;
                                            setWatchForm({ ...watchForm, log_group_names: newNames });
                                        }}
                                        placeholder="/aws/lambda/my-function"
                                    />
                                    {watchForm.log_group_names.length > 1 && (
                                        <Button
                                            variant="outline"
                                            size="icon"
                                            onClick={() => {
                                                const newNames = watchForm.log_group_names.filter((_, i) => i !== index);
                                                setWatchForm({ ...watchForm, log_group_names: newNames });
                                            }}
                                        >
                                            <Trash2 className="w-4 h-4" />
                                        </Button>
                                    )}
                                </div>
                            ))}
                            <Button
                                variant="outline"
                                size="sm"
                                onClick={() => setWatchForm({ ...watchForm, log_group_names: [...watchForm.log_group_names, ''] })}
                            >
                                Add Log Group
                            </Button>
                        </div>

                        <div>
                            <label htmlFor="time-range" className="text-sm font-medium mb-2 block">Time Range (minutes)</label>
                            <Input
                                id="time-range"
                                type="number"
                                value={watchForm.time_range_minutes}
                                onChange={(e) => setWatchForm({ ...watchForm, time_range_minutes: Number.parseInt(e.target.value, 10) })}
                            />
                        </div>

                        <div>
                            <label htmlFor="filter-pattern" className="text-sm font-medium mb-2 block">Filter Pattern (optional)</label>
                            <Input
                                id="filter-pattern"
                                value={watchForm.filter_pattern}
                                onChange={(e) => setWatchForm({ ...watchForm, filter_pattern: e.target.value })}
                                placeholder="[timestamp, message, level=ERROR*]"
                            />
                        </div>

                        <div>
                            <label htmlFor="region" className="text-sm font-medium mb-2 block">Region</label>
                            <select
                                id="region"
                                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                                value={watchForm.region}
                                onChange={(e) => setWatchForm({ ...watchForm, region: e.target.value })}
                            >
                                <option value="us-east-1">us-east-1</option>
                                <option value="us-west-2">us-west-2</option>
                                <option value="eu-west-1">eu-west-1</option>
                            </select>
                        </div>
                    </div>

                    <DialogFooter>
                        <Button variant="outline" onClick={() => setWatchDialogOpen(false)}>Cancel</Button>
                        <Button onClick={handleWatchLogs}>Watch</Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            {/* Alert Dialog */}
            <Dialog open={alertDialogOpen} onOpenChange={setAlertDialogOpen}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>Create Alert</DialogTitle>
                        <DialogDescription>Create a new alert configuration</DialogDescription>
                    </DialogHeader>
                    
                    <div className="space-y-4 py-4">
                        <div>
                            <label htmlFor="alert-log-group" className="text-sm font-medium mb-2 block">Log Group</label>
                            <Input
                                id="alert-log-group"
                                value={alertForm.log_group}
                                onChange={(e) => setAlertForm({ ...alertForm, log_group: e.target.value })}
                            />
                        </div>

                        <div>
                            <label htmlFor="alert-type" className="text-sm font-medium mb-2 block">Alert Type</label>
                            <select
                                id="alert-type"
                                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                                value={alertForm.alert_type}
                                onChange={(e) => setAlertForm({ ...alertForm, alert_type: e.target.value })}
                            >
                                <option value="anomaly">Anomaly</option>
                                <option value="pattern_match">Pattern Match</option>
                                <option value="error_spike">Error Spike</option>
                                <option value="correlation">Correlation</option>
                            </select>
                        </div>

                        <div>
                            <label htmlFor="alert-severity" className="text-sm font-medium mb-2 block">Severity</label>
                            <select
                                id="alert-severity"
                                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                                value={alertForm.severity}
                                onChange={(e) => setAlertForm({ ...alertForm, severity: e.target.value })}
                            >
                                <option value="critical">Critical</option>
                                <option value="high">High</option>
                                <option value="medium">Medium</option>
                                <option value="low">Low</option>
                                <option value="info">Info</option>
                            </select>
                        </div>

                        <div>
                            <label htmlFor="alert-message" className="text-sm font-medium mb-2 block">Message</label>
                            <textarea
                                id="alert-message"
                                className="flex min-h-[80px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                                value={alertForm.message}
                                onChange={(e) => setAlertForm({ ...alertForm, message: e.target.value })}
                                rows={3}
                            />
                        </div>
                    </div>

                    <DialogFooter>
                        <Button variant="outline" onClick={() => setAlertDialogOpen(false)}>Cancel</Button>
                        <Button onClick={handleCreateAlert}>Create</Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            {/* Pattern Dialog */}
            <Dialog open={patternDialogOpen} onOpenChange={setPatternDialogOpen}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>Add Log Pattern</DialogTitle>
                        <DialogDescription>Define a pattern to match in log messages</DialogDescription>
                    </DialogHeader>
                    
                    <div className="space-y-4 py-4">
                        <div>
                            <label htmlFor="pattern-name" className="text-sm font-medium mb-2 block">Pattern Name</label>
                            <Input
                                id="pattern-name"
                                value={patternForm.name}
                                onChange={(e) => setPatternForm({ ...patternForm, name: e.target.value })}
                            />
                        </div>

                        <div>
                            <label htmlFor="pattern-regex" className="text-sm font-medium mb-2 block">Pattern (regex or text)</label>
                            <Input
                                id="pattern-regex"
                                value={patternForm.pattern}
                                onChange={(e) => setPatternForm({ ...patternForm, pattern: e.target.value })}
                            />
                        </div>

                        <div>
                            <label htmlFor="pattern-type" className="text-sm font-medium mb-2 block">Pattern Type</label>
                            <select
                                id="pattern-type"
                                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                                value={patternForm.pattern_type}
                                onChange={(e) => setPatternForm({ ...patternForm, pattern_type: e.target.value })}
                            >
                                <option value="error">Error</option>
                                <option value="warning">Warning</option>
                                <option value="info">Info</option>
                                <option value="custom">Custom</option>
                            </select>
                        </div>

                        <div>
                            <label htmlFor="pattern-severity" className="text-sm font-medium mb-2 block">Severity (1-5)</label>
                            <Input
                                id="pattern-severity"
                                type="number"
                                min="1"
                                max="5"
                                value={patternForm.severity}
                                onChange={(e) => setPatternForm({ ...patternForm, severity: Number.parseInt(e.target.value, 10) })}
                            />
                        </div>

                        <div>
                            <label htmlFor="pattern-description" className="text-sm font-medium mb-2 block">Description</label>
                            <textarea
                                id="pattern-description"
                                className="flex min-h-[60px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                                value={patternForm.description}
                                onChange={(e) => setPatternForm({ ...patternForm, description: e.target.value })}
                            />
                        </div>
                    </div>

                    <DialogFooter>
                        <Button variant="outline" onClick={() => setPatternDialogOpen(false)}>Cancel</Button>
                        <Button onClick={handleAddPattern}>Add</Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            {/* Baseline Dialog */}
            <Dialog open={baselineDialogOpen} onOpenChange={setBaselineDialogOpen}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>Add Baseline Metric</DialogTitle>
                        <DialogDescription>Configure a baseline for anomaly detection</DialogDescription>
                    </DialogHeader>
                    
                    <div className="space-y-4 py-4">
                        <div>
                            <label htmlFor="metric-name" className="text-sm font-medium mb-2 block">Metric Name</label>
                            <Input
                                id="metric-name"
                                value={baselineForm.metric_name}
                                onChange={(e) => setBaselineForm({ ...baselineForm, metric_name: e.target.value })}
                            />
                        </div>

                        <div>
                            <label htmlFor="baseline-log-group" className="text-sm font-medium mb-2 block">Log Group</label>
                            <Input
                                id="baseline-log-group"
                                value={baselineForm.log_group}
                                onChange={(e) => setBaselineForm({ ...baselineForm, log_group: e.target.value })}
                            />
                        </div>

                        <div className="grid grid-cols-2 gap-4">
                            <div>
                                <label htmlFor="normal-min" className="text-sm font-medium mb-2 block">Normal Min</label>
                                <Input
                                    id="normal-min"
                                    type="number"
                                    value={baselineForm.normal_range_min}
                                    onChange={(e) => setBaselineForm({ ...baselineForm, normal_range_min: Number.parseFloat(e.target.value) })}
                                />
                            </div>
                            <div>
                                <label htmlFor="normal-max" className="text-sm font-medium mb-2 block">Normal Max</label>
                                <Input
                                    id="normal-max"
                                    type="number"
                                    value={baselineForm.normal_range_max}
                                    onChange={(e) => setBaselineForm({ ...baselineForm, normal_range_max: Number.parseFloat(e.target.value) })}
                                />
                            </div>
                        </div>

                        <div className="grid grid-cols-2 gap-4">
                            <div>
                                <label htmlFor="threshold-warning" className="text-sm font-medium mb-2 block">Warning Threshold</label>
                                <Input
                                    id="threshold-warning"
                                    type="number"
                                    value={baselineForm.threshold_warning}
                                    onChange={(e) => setBaselineForm({ ...baselineForm, threshold_warning: Number.parseFloat(e.target.value) })}
                                />
                            </div>
                            <div>
                                <label htmlFor="threshold-critical" className="text-sm font-medium mb-2 block">Critical Threshold</label>
                                <Input
                                    id="threshold-critical"
                                    type="number"
                                    value={baselineForm.threshold_critical}
                                    onChange={(e) => setBaselineForm({ ...baselineForm, threshold_critical: Number.parseFloat(e.target.value) })}
                                />
                            </div>
                        </div>

                        <div>
                            <label htmlFor="time-window" className="text-sm font-medium mb-2 block">Time Window</label>
                            <select
                                id="time-window"
                                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                                value={baselineForm.time_window}
                                onChange={(e) => setBaselineForm({ ...baselineForm, time_window: e.target.value })}
                            >
                                <option value="1m">1 minute</option>
                                <option value="5m">5 minutes</option>
                                <option value="15m">15 minutes</option>
                                <option value="1h">1 hour</option>
                            </select>
                        </div>
                    </div>

                    <DialogFooter>
                        <Button variant="outline" onClick={() => setBaselineDialogOpen(false)}>Cancel</Button>
                        <Button onClick={handleAddBaseline}>Add</Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            {/* Known Issue Dialog */}
            <Dialog open={issueDialogOpen} onOpenChange={setIssueDialogOpen}>
                <DialogContent className="max-w-2xl">
                    <DialogHeader>
                        <DialogTitle>Add Known Issue</DialogTitle>
                        <DialogDescription>Document a known issue and its resolution</DialogDescription>
                    </DialogHeader>
                    
                    <div className="space-y-4 py-4">
                        <div>
                            <label htmlFor="issue-title" className="text-sm font-medium mb-2 block">Title</label>
                            <Input
                                id="issue-title"
                                value={issueForm.title}
                                onChange={(e) => setIssueForm({ ...issueForm, title: e.target.value })}
                            />
                        </div>

                        <div>
                            <label htmlFor="issue-description" className="text-sm font-medium mb-2 block">Description</label>
                            <textarea
                                id="issue-description"
                                className="flex min-h-[60px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                                value={issueForm.description}
                                onChange={(e) => setIssueForm({ ...issueForm, description: e.target.value })}
                                rows={2}
                            />
                        </div>

                        <div>
                            <label htmlFor="issue-symptoms" className="text-sm font-medium mb-2 block">Symptoms</label>
                            {issueForm.symptoms.map((symptom, index) => (
                                <div key={`symptom-${index}`} className="flex gap-2 mb-2">
                                    <Input
                                        value={symptom}
                                        onChange={(e) => {
                                            const newSymptoms = [...issueForm.symptoms];
                                            newSymptoms[index] = e.target.value;
                                            setIssueForm({ ...issueForm, symptoms: newSymptoms });
                                        }}
                                    />
                                    {issueForm.symptoms.length > 1 && (
                                        <Button
                                            variant="outline"
                                            size="icon"
                                            onClick={() => {
                                                const newSymptoms = issueForm.symptoms.filter((_, i) => i !== index);
                                                setIssueForm({ ...issueForm, symptoms: newSymptoms });
                                            }}
                                        >
                                            <Trash2 className="w-4 h-4" />
                                        </Button>
                                    )}
                                </div>
                            ))}
                            <Button
                                variant="outline"
                                size="sm"
                                onClick={() => setIssueForm({ ...issueForm, symptoms: [...issueForm.symptoms, ''] })}
                            >
                                Add Symptom
                            </Button>
                        </div>

                        <div>
                            <label htmlFor="issue-solution" className="text-sm font-medium mb-2 block">Solution</label>
                            <textarea
                                id="issue-solution"
                                className="flex min-h-[80px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                                value={issueForm.solution}
                                onChange={(e) => setIssueForm({ ...issueForm, solution: e.target.value })}
                                rows={3}
                            />
                        </div>

                        <div>
                            <label htmlFor="issue-category" className="text-sm font-medium mb-2 block">Category</label>
                            <Input
                                id="issue-category"
                                value={issueForm.category}
                                onChange={(e) => setIssueForm({ ...issueForm, category: e.target.value })}
                            />
                        </div>
                    </div>

                    <DialogFooter>
                        <Button variant="outline" onClick={() => setIssueDialogOpen(false)}>Cancel</Button>
                        <Button onClick={handleAddKnownIssue}>Add</Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
}
