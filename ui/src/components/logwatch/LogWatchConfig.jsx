import React, { useState, useEffect } from 'react';
import {
    Box,
    Typography,
    TextField,
    Button,
    Chip,
    Paper,
    Table,
    TableBody,
    TableCell,
    TableContainer,
    TableHead,
    TableRow,
    IconButton,
    Dialog,
    DialogTitle,
    DialogContent,
    DialogActions,
    FormControl,
    InputLabel,
    Select,
    MenuItem,
    Alert,
    Snackbar,
    Tab,
    Tabs,
    Card,
    CardContent,
    CardActions,
    Grid,
    LinearProgress,
    Tooltip
} from '@mui/material';
import {
    Add as AddIcon,
    Delete as DeleteIcon,
    Edit as EditIcon,
    PlayArrow as PlayArrowIcon,
    Refresh as RefreshIcon,
    Warning as WarningIcon,
    Error as ErrorIcon,
    Info as InfoIcon,
    CheckCircle as CheckCircleIcon,
    Search as SearchIcon
} from '@mui/icons-material';

const API_BASE_URL = 'http://localhost:8000/api/v1';

// Tab Panel Component
function TabPanel({ children, value, index, ...other }) {
    return (
        <div hidden={value !== index} {...other}>
            {value === index && <Box sx={{ pt: 2 }}>{children}</Box>}
        </div>
    );
}

// Severity Chip Component
function SeverityChip({ severity }) {
    const colors = {
        critical: { bg: '#ffebee', color: '#c62828', icon: ErrorIcon },
        high: { bg: '#fff3e0', color: '#ef6c00', icon: WarningIcon },
        medium: { bg: '#fff8e1', color: '#f9a825', icon: WarningIcon },
        low: { bg: '#e3f2fd', color: '#1565c0', icon: InfoIcon },
        info: { bg: '#e8f5e9', color: '#2e7d32', icon: InfoIcon }
    };

    const config = colors[severity] || colors.info;
    const Icon = config.icon;

    return (
        <Chip
            icon={<Icon sx={{ fontSize: 16 }} />}
            label={severity.toUpperCase()}
            size="small"
            sx={{
                backgroundColor: config.bg,
                color: config.color,
                fontWeight: 600
            }}
        />
    );
}

// Status Chip Component
function StatusChip({ status }) {
    const colors = {
        new: { bg: '#ffebee', color: '#c62828' },
        acknowledged: { bg: '#fff3e0', color: '#ef6c00' },
        resolved: { bg: '#e8f5e9', color: '#2e7d32' },
        dismissed: { bg: '#f5f5f5', color: '#757575' }
    };

    const config = colors[status] || colors.new;

    return (
        <Chip
            label={status.toUpperCase()}
            size="small"
            sx={{
                backgroundColor: config.bg,
                color: config.color,
                fontWeight: 600
            }}
        />
    );
}

// Log Watch Configuration Component
export default function LogWatchConfig() {
    const [tabValue, setTabValue] = useState(0);
    const [alerts, setAlerts] = useState([]);
    const [alertSummary, setAlertSummary] = useState(null);
    const [knownIssues, setKnownIssues] = useState([]);
    const [patterns, setPatterns] = useState([]);
    const [baselines, setBaselines] = useState([]);
    const [loading, setLoading] = useState(false);
    const [snackbar, setSnackbar] = useState({ open: false, message: '', severity: 'success' });

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
        message: '',
        details: {}
    });

    const [patternForm, setPatternForm] = useState({
        name: '',
        pattern: '',
        pattern_type: 'error',
        severity: 1,
        description: ''
    });

    const [baselineForm, setBaselineForm] = useState({
        metric_name: '',
        log_group: '',
        normal_range_min: 0,
        normal_range_max: 100,
        threshold_warning: 80,
        threshold_critical: 95,
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
            setSnackbar({ open: true, message: 'Log watch completed', severity: 'success' });
        } catch (error) {
            setSnackbar({ open: true, message: 'Error watching logs', severity: 'error' });
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
            setSnackbar({ open: true, message: 'Anomaly detection completed', severity: 'success' });
        } catch (error) {
            setSnackbar({ open: true, message: 'Error detecting anomalies', severity: 'error' });
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
                setSnackbar({ open: true, message: 'Alert created', severity: 'success' });
                fetchAlerts();
                fetchAlertSummary();
            }
        } catch (error) {
            setSnackbar({ open: true, message: 'Error creating alert', severity: 'error' });
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
                setSnackbar({ open: true, message: 'Alert acknowledged', severity: 'success' });
                fetchAlerts();
            }
        } catch (error) {
            setSnackbar({ open: true, message: 'Error acknowledging alert', severity: 'error' });
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
                setSnackbar({ open: true, message: 'Alert resolved', severity: 'success' });
                fetchAlerts();
                fetchAlertSummary();
            }
        } catch (error) {
            setSnackbar({ open: true, message: 'Error resolving alert', severity: 'error' });
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
                setSnackbar({ open: true, message: 'Pattern added', severity: 'success' });
                fetchPatterns();
            }
        } catch (error) {
            setSnackbar({ open: true, message: 'Error adding pattern', severity: 'error' });
        }
        setPatternDialogOpen(false);
    };

    // Add Baseline
    const handleAddBaseline = async () => {
        try {
            const response = await fetch(`${API_BASE_URL}/log-watch/baselines`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(baselineForm)
            });
            const data = await response.json();
            if (data.id) {
                setSnackbar({ open: true, message: 'Baseline set', severity: 'success' });
                fetchBaselines();
            }
        } catch (error) {
            setSnackbar({ open: true, message: 'Error setting baseline', severity: 'error' });
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
                setSnackbar({ open: true, message: 'Known issue added', severity: 'success' });
                fetchKnownIssues();
            }
        } catch (error) {
            setSnackbar({ open: true, message: 'Error adding known issue', severity: 'error' });
        }
        setIssueDialogOpen(false);
    };

    return (
        <Box sx={{ p: 3 }}>
            <Typography variant="h4" gutterBottom>
                CloudWatch Log Watch Analyzer
            </Typography>

            {/* Alert Summary Cards */}
            {alertSummary && (
                <Grid container spacing={2} sx={{ mb: 3 }}>
                    <Grid item xs={12} sm={6} md={2}>
                        <Card>
                            <CardContent>
                                <Typography color="textSecondary" variant="body2">Total Alerts</Typography>
                                <Typography variant="h4">{alertSummary.total_alerts}</Typography>
                            </CardContent>
                        </Card>
                    </Grid>
                    <Grid item xs={12} sm={6} md={2}>
                        <Card sx={{ bgcolor: '#ffebee' }}>
                            <CardContent>
                                <Typography color="error" variant="body2">Critical</Typography>
                                <Typography variant="h4" color="error">{alertSummary.by_severity?.critical || 0}</Typography>
                            </CardContent>
                        </Card>
                    </Grid>
                    <Grid item xs={12} sm={6} md={2}>
                        <Card sx={{ bgcolor: '#fff3e0' }}>
                            <CardContent>
                                <Typography color="warning.main" variant="body2">High</Typography>
                                <Typography variant="h4" color="warning.main">{alertSummary.by_severity?.high || 0}</Typography>
                            </CardContent>
                        </Card>
                    </Grid>
                    <Grid item xs={12} sm={6} md={2}>
                        <Card sx={{ bgcolor: '#e3f2fd' }}>
                            <CardContent>
                                <Typography color="info.main" variant="body2">New</Typography>
                                <Typography variant="h4" color="info.main">{alertSummary.by_status?.new || 0}</Typography>
                            </CardContent>
                        </Card>
                    </Grid>
                    <Grid item xs={12} sm={6} md={2}>
                        <Card sx={{ bgcolor: '#fff8e1' }}>
                            <CardContent>
                                <Typography color="textSecondary" variant="body2">Acknowledged</Typography>
                                <Typography variant="h4">{alertSummary.by_status?.acknowledged || 0}</Typography>
                            </CardContent>
                        </Card>
                    </Grid>
                    <Grid item xs={12} sm={6} md={2}>
                        <Card sx={{ bgcolor: '#e8f5e9' }}>
                            <CardContent>
                                <Typography color="success.main" variant="body2">Resolved</Typography>
                                <Typography variant="h4" color="success.main">{alertSummary.by_status?.resolved || 0}</Typography>
                            </CardContent>
                        </Card>
                    </Grid>
                </Grid>
            )}

            {/* Tabs */}
            <Paper sx={{ mb: 2 }}>
                <Tabs value={tabValue} onChange={(e, v) => setTabValue(v)}>
                    <Tab label="Alerts" />
                    <Tab label="Watch Logs" />
                    <Tab label="Patterns" />
                    <Tab label="Baselines" />
                    <Tab label="Known Issues" />
                </Tabs>
            </Paper>

            {loading && <LinearProgress />}

            {/* Alerts Tab */}
            <TabPanel value={tabValue} index={0}>
                <Box sx={{ mb: 2 }}>
                    <Button
                        variant="contained"
                        startIcon={<AddIcon />}
                        onClick={() => setAlertDialogOpen(true)}
                    >
                        Create Alert
                    </Button>
                    <Button
                        variant="outlined"
                        startIcon={<RefreshIcon />}
                        onClick={fetchAlerts}
                        sx={{ ml: 1 }}
                    >
                        Refresh
                    </Button>
                </Box>

                <TableContainer component={Paper}>
                    <Table>
                        <TableHead>
                            <TableRow>
                                <TableCell>ID</TableCell>
                                <TableCell>Log Group</TableCell>
                                <TableCell>Type</TableCell>
                                <TableCell>Severity</TableCell>
                                <TableCell>Status</TableCell>
                                <TableCell>Message</TableCell>
                                <TableCell>Created</TableCell>
                                <TableCell>Actions</TableCell>
                            </TableRow>
                        </TableHead>
                        <TableBody>
                            {alerts.map((alert) => (
                                <TableRow key={alert.id}>
                                    <TableCell>{alert.id}</TableCell>
                                    <TableCell>{alert.log_group}</TableCell>
                                    <TableCell>{alert.alert_type}</TableCell>
                                    <TableCell><SeverityChip severity={alert.severity} /></TableCell>
                                    <TableCell><StatusChip status={alert.status} /></TableCell>
                                    <TableCell sx={{ maxWidth: 300, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                                        {alert.message}
                                    </TableCell>
                                    <TableCell>
                                        {alert.created_at ? new Date(alert.created_at).toLocaleString() : '-'}
                                    </TableCell>
                                    <TableCell>
                                        {alert.status === 'new' && (
                                            <Button size="small" onClick={() => handleAcknowledgeAlert(alert.id)}>
                                                Ack
                                            </Button>
                                        )}
                                        {alert.status !== 'resolved' && alert.status !== 'dismissed' && (
                                            <Button size="small" color="success" onClick={() => handleResolveAlert(alert.id)}>
                                                Resolve
                                            </Button>
                                        )}
                                    </TableCell>
                                </TableRow>
                            ))}
                        </TableBody>
                    </Table>
                </TableContainer>
            </TabPanel>

            {/* Watch Logs Tab */}
            <TabPanel value={tabValue} index={1}>
                <Box sx={{ mb: 2 }}>
                    <Button
                        variant="contained"
                        startIcon={<PlayArrowIcon />}
                        onClick={() => setWatchDialogOpen(true)}
                    >
                        Watch Log Groups
                    </Button>
                    <Button
                        variant="outlined"
                        startIcon={<SearchIcon />}
                        onClick={handleDetectAnomalies}
                        sx={{ ml: 1 }}
                    >
                        Detect Anomalies
                    </Button>
                </Box>

                {watchResults && (
                    <Paper sx={{ p: 2, mb: 2 }}>
                        <Typography variant="h6">Watch Results</Typography>
                        <Typography variant="body2" color="textSecondary">
                            Total Events: {watchResults.summary?.total_events || 0}
                        </Typography>
                        <pre style={{ maxHeight: 300, overflow: 'auto' }}>
                            {JSON.stringify(watchResults.log_groups, null, 2)}
                        </pre>
                    </Paper>
                )}

                {anomalyResults && (
                    <Paper sx={{ p: 2 }}>
                        <Typography variant="h6">Anomaly Detection Results</Typography>
                        <Typography variant="body2" color="textSecondary">
                            Total Anomalies: {anomalyResults.summary?.total_anomalies || 0}
                        </Typography>
                        <pre style={{ maxHeight: 300, overflow: 'auto' }}>
                            {JSON.stringify(anomalyResults.anomalies, null, 2)}
                        </pre>
                    </Paper>
                )}
            </TabPanel>

            {/* Patterns Tab */}
            <TabPanel value={tabValue} index={2}>
                <Box sx={{ mb: 2 }}>
                    <Button
                        variant="contained"
                        startIcon={<AddIcon />}
                        onClick={() => setPatternDialogOpen(true)}
                    >
                        Add Pattern
                    </Button>
                </Box>

                <TableContainer component={Paper}>
                    <Table>
                        <TableHead>
                            <TableRow>
                                <TableCell>Name</TableCell>
                                <TableCell>Pattern</TableCell>
                                <TableCell>Type</TableCell>
                                <TableCell>Severity</TableCell>
                                <TableCell>Description</TableCell>
                            </TableRow>
                        </TableHead>
                        <TableBody>
                            {patterns.map((pattern) => (
                                <TableRow key={pattern.id}>
                                    <TableCell>{pattern.name}</TableCell>
                                    <TableCell sx={{ fontFamily: 'monospace', fontSize: 12 }}>
                                        {pattern.pattern}
                                    </TableCell>
                                    <TableCell>{pattern.pattern_type}</TableCell>
                                    <TableCell>{pattern.severity}</TableCell>
                                    <TableCell>{pattern.description}</TableCell>
                                </TableRow>
                            ))}
                        </TableBody>
                    </Table>
                </TableContainer>
            </TabPanel>

            {/* Baselines Tab */}
            <TabPanel value={tabValue} index={3}>
                <Box sx={{ mb: 2 }}>
                    <Button
                        variant="contained"
                        startIcon={<AddIcon />}
                        onClick={() => setBaselineDialogOpen(true)}
                    >
                        Add Baseline
                    </Button>
                </Box>

                <TableContainer component={Paper}>
                    <Table>
                        <TableHead>
                            <TableRow>
                                <TableCell>Metric</TableCell>
                                <TableCell>Log Group</TableCell>
                                <TableCell>Normal Range</TableCell>
                                <TableCell>Warning</TableCell>
                                <TableCell>Critical</TableCell>
                                <TableCell>Window</TableCell>
                            </TableRow>
                        </TableHead>
                        <TableBody>
                            {baselines.map((baseline) => (
                                <TableRow key={baseline.id}>
                                    <TableCell>{baseline.metric_name}</TableCell>
                                    <TableCell>{baseline.log_group}</TableCell>
                                    <TableCell>
                                        {baseline.normal_range?.min} - {baseline.normal_range?.max}
                                    </TableCell>
                                    <TableCell>{baseline.thresholds?.warning}</TableCell>
                                    <TableCell>{baseline.thresholds?.critical}</TableCell>
                                    <TableCell>{baseline.time_window}</TableCell>
                                </TableRow>
                            ))}
                        </TableBody>
                    </Table>
                </TableContainer>
            </TabPanel>

            {/* Known Issues Tab */}
            <TabPanel value={tabValue} index={4}>
                <Box sx={{ mb: 2 }}>
                    <Button
                        variant="contained"
                        startIcon={<AddIcon />}
                        onClick={() => setIssueDialogOpen(true)}
                    >
                        Add Known Issue
                    </Button>
                </Box>

                <Grid container spacing={2}>
                    {knownIssues.map((issue) => (
                        <Grid item xs={12} md={6} key={issue.id}>
                            <Card>
                                <CardContent>
                                    <Typography variant="h6">{issue.title}</Typography>
                                    <Chip label={issue.category} size="small" sx={{ mb: 1 }} />
                                    <Typography variant="body2" color="textSecondary">
                                        {issue.description}
                                    </Typography>
                                    <Typography variant="body2" sx={{ mt: 1 }}>
                                        <strong>Symptoms:</strong> {issue.symptoms?.join(', ')}
                                    </Typography>
                                    <Typography variant="body2" sx={{ mt: 1 }}>
                                        <strong>Solution:</strong> {issue.solution}
                                    </Typography>
                                </CardContent>
                            </Card>
                        </Grid>
                    ))}
                </Grid>
            </TabPanel>

            {/* Watch Dialog */}
            <Dialog open={watchDialogOpen} onClose={() => setWatchDialogOpen(false)} maxWidth="md" fullWidth>
                <DialogTitle>Watch Log Groups</DialogTitle>
                <DialogContent>
                    <Box sx={{ pt: 1 }}>
                        <Typography variant="subtitle2">Log Group Names</Typography>
                        {watchForm.log_group_names.map((name, index) => (
                            <Box key={index} sx={{ display: 'flex', gap: 1, mb: 1 }}>
                                <TextField
                                    fullWidth
                                    value={name}
                                    onChange={(e) => {
                                        const newNames = [...watchForm.log_group_names];
                                        newNames[index] = e.target.value;
                                        setWatchForm({ ...watchForm, log_group_names: newNames });
                                    }}
                                    placeholder="/aws/lambda/my-function"
                                />
                                {watchForm.log_group_names.length > 1 && (
                                    <IconButton onClick={() => {
                                        const newNames = watchForm.log_group_names.filter((_, i) => i !== index);
                                        setWatchForm({ ...watchForm, log_group_names: newNames });
                                    }}>
                                        <DeleteIcon />
                                    </IconButton>
                                )}
                            </Box>
                        ))}
                        <Button
                            onClick={() => setWatchForm({ ...watchForm, log_group_names: [...watchForm.log_group_names, ''] })}
                        >
                            Add Log Group
                        </Button>

                        <TextField
                            fullWidth
                            label="Time Range (minutes)"
                            type="number"
                            value={watchForm.time_range_minutes}
                            onChange={(e) => setWatchForm({ ...watchForm, time_range_minutes: parseInt(e.target.value) })}
                            sx={{ mt: 2 }}
                        />

                        <TextField
                            fullWidth
                            label="Filter Pattern (optional)"
                            value={watchForm.filter_pattern}
                            onChange={(e) => setWatchForm({ ...watchForm, filter_pattern: e.target.value })}
                            sx={{ mt: 2 }}
                            placeholder="[timestamp, message, level=ERROR*]"
                        />

                        <FormControl fullWidth sx={{ mt: 2 }}>
                            <InputLabel>Region</InputLabel>
                            <Select
                                value={watchForm.region}
                                onChange={(e) => setWatchForm({ ...watchForm, region: e.target.value })}
                            >
                                <MenuItem value="us-east-1">us-east-1</MenuItem>
                                <MenuItem value="us-west-2">us-west-2</MenuItem>
                                <MenuItem value="eu-west-1">eu-west-1</MenuItem>
                            </Select>
                        </FormControl>
                    </Box>
                </DialogContent>
                <DialogActions>
                    <Button onClick={() => setWatchDialogOpen(false)}>Cancel</Button>
                    <Button onClick={handleWatchLogs} variant="contained">Watch</Button>
                </DialogActions>
            </Dialog>

            {/* Alert Dialog */}
            <Dialog open={alertDialogOpen} onClose={() => setAlertDialogOpen(false)}>
                <DialogTitle>Create Alert</DialogTitle>
                <DialogContent>
                    <TextField
                        fullWidth
                        label="Log Group"
                        value={alertForm.log_group}
                        onChange={(e) => setAlertForm({ ...alertForm, log_group: e.target.value })}
                        sx={{ mt: 1 }}
                    />
                    <FormControl fullWidth sx={{ mt: 2 }}>
                        <InputLabel>Alert Type</InputLabel>
                        <Select
                            value={alertForm.alert_type}
                            onChange={(e) => setAlertForm({ ...alertForm, alert_type: e.target.value })}
                        >
                            <MenuItem value="anomaly">Anomaly</MenuItem>
                            <MenuItem value="pattern_match">Pattern Match</MenuItem>
                            <MenuItem value="error_spike">Error Spike</MenuItem>
                            <MenuItem value="correlation">Correlation</MenuItem>
                        </Select>
                    </FormControl>
                    <FormControl fullWidth sx={{ mt: 2 }}>
                        <InputLabel>Severity</InputLabel>
                        <Select
                            value={alertForm.severity}
                            onChange={(e) => setAlertForm({ ...alertForm, severity: e.target.value })}
                        >
                            <MenuItem value="critical">Critical</MenuItem>
                            <MenuItem value="high">High</MenuItem>
                            <MenuItem value="medium">Medium</MenuItem>
                            <MenuItem value="low">Low</MenuItem>
                            <MenuItem value="info">Info</MenuItem>
                        </Select>
                    </FormControl>
                    <TextField
                        fullWidth
                        label="Message"
                        multiline
                        rows={3}
                        value={alertForm.message}
                        onChange={(e) => setAlertForm({ ...alertForm, message: e.target.value })}
                        sx={{ mt: 2 }}
                    />
                </DialogContent>
                <DialogActions>
                    <Button onClick={() => setAlertDialogOpen(false)}>Cancel</Button>
                    <Button onClick={handleCreateAlert} variant="contained">Create</Button>
                </DialogActions>
            </Dialog>

            {/* Pattern Dialog */}
            <Dialog open={patternDialogOpen} onClose={() => setPatternDialogOpen(false)}>
                <DialogTitle>Add Log Pattern</DialogTitle>
                <DialogContent>
                    <TextField
                        fullWidth
                        label="Pattern Name"
                        value={patternForm.name}
                        onChange={(e) => setPatternForm({ ...patternForm, name: e.target.value })}
                        sx={{ mt: 1 }}
                    />
                    <TextField
                        fullWidth
                        label="Pattern (regex or text)"
                        value={patternForm.pattern}
                        onChange={(e) => setPatternForm({ ...patternForm, pattern: e.target.value })}
                        sx={{ mt: 2 }}
                    />
                    <FormControl fullWidth sx={{ mt: 2 }}>
                        <InputLabel>Pattern Type</InputLabel>
                        <Select
                            value={patternForm.pattern_type}
                            onChange={(e) => setPatternForm({ ...patternForm, pattern_type: e.target.value })}
                        >
                            <MenuItem value="error">Error</MenuItem>
                            <MenuItem value="warning">Warning</MenuItem>
                            <MenuItem value="info">Info</MenuItem>
                            <MenuItem value="custom">Custom</MenuItem>
                        </Select>
                    </FormControl>
                    <TextField
                        fullWidth
                        label="Severity (1-5)"
                        type="number"
                        value={patternForm.severity}
                        onChange={(e) => setPatternForm({ ...patternForm, severity: parseInt(e.target.value) })}
                        sx={{ mt: 2 }}
                        inputProps={{ min: 1, max: 5 }}
                    />
                    <TextField
                        fullWidth
                        label="Description"
                        multiline
                        value={patternForm.description}
                        onChange={(e) => setPatternForm({ ...patternForm, description: e.target.value })}
                        sx={{ mt: 2 }}
                    />
                </DialogContent>
                <DialogActions>
                    <Button onClick={() => setPatternDialogOpen(false)}>Cancel</Button>
                    <Button onClick={handleAddPattern} variant="contained">Add</Button>
                </DialogActions>
            </Dialog>

            {/* Baseline Dialog */}
            <Dialog open={baselineDialogOpen} onClose={() => setBaselineDialogOpen(false)}>
                <DialogTitle>Add Baseline Metric</DialogTitle>
                <DialogContent>
                    <TextField
                        fullWidth
                        label="Metric Name"
                        value={baselineForm.metric_name}
                        onChange={(e) => setBaselineForm({ ...baselineForm, metric_name: e.target.value })}
                        sx={{ mt: 1 }}
                    />
                    <TextField
                        fullWidth
                        label="Log Group"
                        value={baselineForm.log_group}
                        onChange={(e) => setBaselineForm({ ...baselineForm, log_group: e.target.value })}
                        sx={{ mt: 2 }}
                    />
                    <Grid container spacing={2} sx={{ mt: 1 }}>
                        <Grid item xs={6}>
                            <TextField
                                fullWidth
                                label="Normal Min"
                                type="number"
                                value={baselineForm.normal_range_min}
                                onChange={(e) => setBaselineForm({ ...baselineForm, normal_range_min: parseFloat(e.target.value) })}
                            />
                        </Grid>
                        <Grid item xs={6}>
                            <TextField
                                fullWidth
                                label="Normal Max"
                                type="number"
                                value={baselineForm.normal_range_max}
                                onChange={(e) => setBaselineForm({ ...baselineForm, normal_range_max: parseFloat(e.target.value) })}
                            />
                        </Grid>
                    </Grid>
                    <Grid container spacing={2} sx={{ mt: 1 }}>
                        <Grid item xs={6}>
                            <TextField
                                fullWidth
                                label="Warning Threshold"
                                type="number"
                                value={baselineForm.threshold_warning}
                                onChange={(e) => setBaselineForm({ ...baselineForm, threshold_warning: parseFloat(e.target.value) })}
                            />
                        </Grid>
                        <Grid item xs={6}>
                            <TextField
                                fullWidth
                                label="Critical Threshold"
                                type="number"
                                value={baselineForm.threshold_critical}
                                onChange={(e) => setBaselineForm({ ...baselineForm, threshold_critical: parseFloat(e.target.value) })}
                            />
                        </Grid>
                    </Grid>
                    <FormControl fullWidth sx={{ mt: 2 }}>
                        <InputLabel>Time Window</InputLabel>
                        <Select
                            value={baselineForm.time_window}
                            onChange={(e) => setBaselineForm({ ...baselineForm, time_window: e.target.value })}
                        >
                            <MenuItem value="1m">1 minute</MenuItem>
                            <MenuItem value="5m">5 minutes</MenuItem>
                            <MenuItem value="15m">15 minutes</MenuItem>
                            <MenuItem value="1h">1 hour</MenuItem>
                        </Select>
                    </FormControl>
                </DialogContent>
                <DialogActions>
                    <Button onClick={() => setBaselineDialogOpen(false)}>Cancel</Button>
                    <Button onClick={handleAddBaseline} variant="contained">Add</Button>
                </DialogActions>
            </Dialog>

            {/* Known Issue Dialog */}
            <Dialog open={issueDialogOpen} onClose={() => setIssueDialogOpen(false)} maxWidth="md" fullWidth>
                <DialogTitle>Add Known Issue</DialogTitle>
                <DialogContent>
                    <TextField
                        fullWidth
                        label="Title"
                        value={issueForm.title}
                        onChange={(e) => setIssueForm({ ...issueForm, title: e.target.value })}
                        sx={{ mt: 1 }}
                    />
                    <TextField
                        fullWidth
                        label="Description"
                        multiline
                        rows={2}
                        value={issueForm.description}
                        onChange={(e) => setIssueForm({ ...issueForm, description: e.target.value })}
                        sx={{ mt: 2 }}
                    />
                    <Typography variant="subtitle2" sx={{ mt: 2 }}>Symptoms</Typography>
                    {issueForm.symptoms.map((symptom, index) => (
                        <Box key={index} sx={{ display: 'flex', gap: 1, mb: 1 }}>
                            <TextField
                                fullWidth
                                value={symptom}
                                onChange={(e) => {
                                    const newSymptoms = [...issueForm.symptoms];
                                    newSymptoms[index] = e.target.value;
                                    setIssueForm({ ...issueForm, symptoms: newSymptoms });
                                }}
                            />
                            {issueForm.symptoms.length > 1 && (
                                <IconButton onClick={() => {
                                    const newSymptoms = issueForm.symptoms.filter((_, i) => i !== index);
                                    setIssueForm({ ...issueForm, symptoms: newSymptoms });
                                }}>
                                    <DeleteIcon />
                                </IconButton>
                            )}
                        </Box>
                    ))}
                    <Button onClick={() => setIssueForm({ ...issueForm, symptoms: [...issueForm.symptoms, ''] })}>
                        Add Symptom
                    </Button>
                    <TextField
                        fullWidth
                        label="Solution"
                        multiline
                        rows={3}
                        value={issueForm.solution}
                        onChange={(e) => setIssueForm({ ...issueForm, solution: e.target.value })}
                        sx={{ mt: 2 }}
                    />
                    <FormControl fullWidth sx={{ mt: 2 }}>
                        <InputLabel>Category</InputLabel>
                        <Select
                            value={issueForm.category}
                            onChange={(e) => setIssueForm({ ...issueForm, category: e.target.value })}
                        >
                            <MenuItem value="database">Database</MenuItem>
                            <MenuItem value="network">Network</MenuItem>
                            <MenuItem value="authentication">Authentication</MenuItem>
                            <MenuItem value="performance">Performance</MenuItem>
                            <MenuItem value="general">General</MenuItem>
                        </Select>
                    </FormControl>
                </DialogContent>
                <DialogActions>
                    <Button onClick={() => setIssueDialogOpen(false)}>Cancel</Button>
                    <Button onClick={handleAddKnownIssue} variant="contained">Add</Button>
                </DialogActions>
            </Dialog>

            {/* Snackbar */}
            <Snackbar
                open={snackbar.open}
                autoHideDuration={3000}
                onClose={() => setSnackbar({ ...snackbar, open: false })}
            >
                <Alert severity={snackbar.severity}>{snackbar.message}</Alert>
            </Snackbar>
        </Box>
    );
}
