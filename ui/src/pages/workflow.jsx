import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import {
  Button,
  TextField,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Paper,
  Chip,
  IconButton,
  Menu,
  MenuItem,
  Alert,
  Box,
  Typography,
  FormControl,
  InputLabel,
  Select,
  AppBar,
  Toolbar
} from '@mui/material';
import {
  Add as AddIcon,
  Edit as EditIcon,
  Delete as DeleteIcon,
  MoreVert as MoreVertIcon,
  Close as CloseIcon,
  Save as SaveIcon,
  PlayArrow as PlayIcon,
  Search as SearchIcon
} from '@mui/icons-material';
import WorkflowEditor from '../components/workflow/WorkflowEditor.jsx';
import { validateWorkflow } from '../utils/workflowValidation.js';
import agentApiClient from '../services/agentApiClient.js';
import { useScheduler } from '../context/SchedulerContext';
import { useWorkflowStatus } from '../context/WorkflowStatusContext';

function Workflow() {
  const {
    schedules: workflows,
    loadSchedules,
    deleteSchedule,
    triggerWorkflow,
    formatTime
  } = useScheduler();
  const { isWorkflowRunning } = useWorkflowStatus();

  const [showDialog, setShowDialog] = useState(false);
  const [showWorkflowEditor, setShowWorkflowEditor] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const [anchorEl, setAnchorEl] = useState(null);
  const [selectedWorkflow, setSelectedWorkflow] = useState(null);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);

  const [workflowName, setWorkflowName] = useState('');
  const [workflowType, setWorkflowType] = useState('workflow');
  const [currentWorkflowData, setCurrentWorkflowData] = useState(null);
  const [editorKey, setEditorKey] = useState(0);
  const [messages, setMessages] = useState([]);

  const workflowEditorRef = useRef(null);

  // Sync with API on mount
  useEffect(() => {
    loadSchedules();
  }, [loadSchedules]);

  const showMessage = useCallback((msg, type = 'info') => {
    const id = Math.random().toString(36).substring(2, 11);
    setMessages(prev => [...prev, { id, msg, type }]);
    setTimeout(() => {
      setMessages(prev => prev.filter(m => m.id !== id));
    }, 4000);
  }, []);

  const handleExecute = async (workflow) => {
    // Prevent duplicate execution if already running
    if (isWorkflowRunning(workflow.name)) {
      showMessage(`Workflow '${workflow.name}' is already running`, 'warning');
      handleMenuClose();
      return;
    }
    
    try {
      await triggerWorkflow(workflow.name);
      showMessage(`Workflow '${workflow.name}' started`, 'success');
      handleMenuClose();
    } catch (error) {
      showMessage(`Failed to start workflow: ${error.message}`, 'error');
    }
  };

  const confirmDelete = async () => {
    if (!selectedWorkflow) return;
    try {
      await deleteSchedule(selectedWorkflow.name);
      showMessage('Workflow deleted successfully', 'success');
    } catch (error) {
      console.error('Error deleting workflow:', error);
      showMessage('Failed to delete workflow', 'error');
    } finally {
      setDeleteDialogOpen(false);
      setSelectedWorkflow(null);
    }
  };

  const openEditDialog = async (workflow) => {
    try {
      // Fetch fresh data for editor
      const fresh = await agentApiClient.getWorkflow(workflow.name);
      setWorkflowName(fresh.name);
      setWorkflowType(fresh.type || 'workflow');
      setCurrentWorkflowData(fresh);
      setEditorKey(prev => prev + 1);
      setShowWorkflowEditor(true);
      handleMenuClose();
    } catch (error) {
      console.error('Error loading workflow:', error);
      showMessage('Failed to load workflow details', 'error');
    }
  };

  const handleProceedToEditor = () => {
    if (!workflowName.trim()) {
      showMessage('Please enter workflow name', 'error');
      return;
    }
    setCurrentWorkflowData(null);
    setEditorKey(prev => prev + 1);
    setShowDialog(false);
    setShowWorkflowEditor(true);
  };

  const handleSaveWorkflowData = async () => {
    if (!workflowEditorRef.current) return;

    const { nodes, edges } = workflowEditorRef.current.getWorkflowData();

    if (!nodes?.length) {
      showMessage('Workflow must have at least one node', 'error');
      return;
    }

    const validation = validateWorkflow(workflowType, nodes, edges);
    if (!validation.isValid) {
      showMessage(validation.error, 'error');
      return;
    }

    try {

      const {
        schedule, enabled, startTime, recurrence, createdAt, updatedAt, ...baseData
      } = currentWorkflowData || {};

      const payload = {
        ...baseData,
        name: workflowName,
        type: workflowType,
        nodes,
        edges,
        updatedAt: new Date().toISOString()
      };

      if (currentWorkflowData) {
        await agentApiClient.updateWorkflow(currentWorkflowData.name, payload);
      } else {
        await agentApiClient.createWorkflow(payload);
      }

      await loadSchedules();
      setShowWorkflowEditor(false);
      showMessage('Workflow saved successfully', 'success');
    } catch (error) {
      showMessage(`Save failed: ${error.message}`, 'error');
    }
  };

  const filteredWorkflows = useMemo(() => {
    const term = searchTerm.toLowerCase();
    return workflows.filter(w => w.name.toLowerCase().includes(term));
  }, [workflows, searchTerm]);

  const handleMenuOpen = (e, w) => {
    setAnchorEl(e.currentTarget);
    setSelectedWorkflow(w);
  };

  const handleMenuClose = () => {
    setAnchorEl(null);
    setSelectedWorkflow(null);
  };

  if (showWorkflowEditor) {
    return (
      <Box sx={{ height: '100vh', display: 'flex', flexDirection: 'column' }}>
        <AppBar position="static" color="default" elevation={1}>
          <Toolbar>
            <Typography variant="h6" sx={{ flexGrow: 1 }}>{workflowName}</Typography>
            <Button startIcon={<CloseIcon />} onClick={() => setShowWorkflowEditor(false)} sx={{ mr: 1 }}>Cancel</Button>
            <Button variant="contained" startIcon={<SaveIcon />} onClick={handleSaveWorkflowData}>Save</Button>
          </Toolbar>
        </AppBar>

        {messages.map(m => (
          <Alert key={m.id} severity={m.type} sx={{ position: 'absolute', top: 70, left: '50%', transform: 'translateX(-50%)', zIndex: 2000 }}>
            {m.msg}
          </Alert>
        ))}

        <Box sx={{ flexGrow: 1, position: 'relative' }}>
          <WorkflowEditor
            key={editorKey}
            ref={workflowEditorRef}
            initialNodes={currentWorkflowData?.nodes || []}
            initialEdges={currentWorkflowData?.edges || []}
          />
        </Box>
      </Box>
    );
  }

  return (
    <Box sx={{ p: 4 }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 4 }}>
        <Box>
          <Typography variant="h4" sx={{ fontWeight: 700 }}>Workflows</Typography>
          <Typography color="text.secondary">Design and manage agentic processes</Typography>
        </Box>
        <Button variant="contained" startIcon={<AddIcon />} onClick={() => { setWorkflowName(''); setShowDialog(true); }}>
          New Workflow
        </Button>
      </Box>

      <Box sx={{ mb: 3, display: 'flex' }}>
        <TextField
          size="small"
          placeholder="Search workflows..."
          value={searchTerm}
          onChange={(e) => setSearchTerm(e.target.value)}
          slotProps={{
            input: {
              startAdornment: (
                <SearchIcon sx={{ color: 'text.secondary', mr: 1, fontSize: 20 }} />
              ),
            },
          }}
          sx={{
            width: '100%',
            maxWidth: 400,
            '& .MuiOutlinedInput-root': {
              borderRadius: 2,
              bgcolor: 'background.paper',
              transition: 'all 0.2s ease-in-out',
              '&:hover': {
                boxShadow: '0 2px 8px rgba(0,0,0,0.05)',
                borderColor: 'primary.main',
              },
              '&.Mui-focused': {
                boxShadow: '0 4px 12px rgba(25, 118, 210, 0.1)',
              }
            }
          }}
        />
      </Box>

      <TableContainer component={Paper} elevation={0} variant="outlined" sx={{ borderRadius: 2 }}>
        <Table>
          <TableHead sx={{ bgcolor: 'action.hover' }}>
            <TableRow>
              <TableCell sx={{ fontWeight: 600 }}>Name</TableCell>
              <TableCell sx={{ fontWeight: 600 }}>Type</TableCell>
              <TableCell sx={{ fontWeight: 600 }}>Schedule</TableCell>
              <TableCell sx={{ fontWeight: 600 }}>Status</TableCell>
              <TableCell align="right" sx={{ fontWeight: 600 }}>Actions</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {filteredWorkflows.map((w) => (
              <TableRow key={w.name} hover>
                <TableCell sx={{ fontWeight: 500 }}>{w.name}</TableCell>
                <TableCell><Chip label={w.type || 'workflow'} size="small" variant="outlined" /></TableCell>
                <TableCell>
                  {w.startTime ? (
                    <Typography variant="body2">
                      {formatTime(w.startTime)}
                      <Typography component="span" variant="caption" sx={{ ml: 1, color: 'text.secondary' }}>
                        ({w.schedule ? w.schedule.split(' ').slice(0, 2).join(':') + ' UTC' : 'Manual'})
                      </Typography>
                    </Typography>
                  ) : (
                    <Typography variant="body2" sx={{ color: 'text.secondary' }}>
                      {w.schedule || 'Manual'}
                    </Typography>
                  )}
                </TableCell>
                <TableCell>
                  <Chip
                    label={w.enabled ? 'Active' : 'Disabled'}
                    size="small"
                    color={w.enabled ? 'success' : 'default'}
                  />
                </TableCell>
                <TableCell align="right">
                  <IconButton size="small" onClick={(e) => handleMenuOpen(e, w)}><MoreVertIcon /></IconButton>
                </TableCell>
              </TableRow>
            ))}
            {filteredWorkflows.length === 0 && (
              <TableRow><TableCell colSpan={5} align="center" sx={{ py: 3 }}>No workflows found</TableCell></TableRow>
            )}
          </TableBody>
        </Table>
      </TableContainer>

      <Menu anchorEl={anchorEl} open={Boolean(anchorEl)} onClose={handleMenuClose}>
        <MenuItem onClick={() => openEditDialog(selectedWorkflow)}><EditIcon fontSize="small" sx={{ mr: 1 }} /> Edit</MenuItem>
        <MenuItem onClick={() => handleExecute(selectedWorkflow)}><PlayIcon fontSize="small" sx={{ mr: 1 }} /> Run Now</MenuItem>
        <MenuItem onClick={() => setDeleteDialogOpen(true)} sx={{ color: 'error.main' }}><DeleteIcon fontSize="small" sx={{ mr: 1 }} /> Delete</MenuItem>
      </Menu>

      <Dialog open={deleteDialogOpen} onClose={() => setDeleteDialogOpen(false)}>
        <DialogTitle>Confirm Delete</DialogTitle>
        <DialogContent>Are you sure you want to delete "{selectedWorkflow?.name}"?</DialogContent>
        <DialogActions>
          <Button onClick={() => setDeleteDialogOpen(false)}>Cancel</Button>
          <Button onClick={confirmDelete} color="error" variant="contained">Delete</Button>
        </DialogActions>
      </Dialog>

      <Dialog open={showDialog} onClose={() => setShowDialog(false)} maxWidth="xs" fullWidth>
        <DialogTitle>Create Workflow</DialogTitle>
        <DialogContent>
          <TextField
            fullWidth
            label="Name"
            value={workflowName}
            onChange={(e) => setWorkflowName(e.target.value)}
            sx={{ mt: 1 }}
            autoFocus
          />
          <FormControl fullWidth sx={{ mt: 2 }}>
            <InputLabel>Type</InputLabel>
            <Select value={workflowType} label="Type" onChange={(e) => setWorkflowType(e.target.value)}>
              <MenuItem value="workflow">Standard Workflow</MenuItem>
              <MenuItem value="agent">Agentic Process</MenuItem>
            </Select>
          </FormControl>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setShowDialog(false)}>Cancel</Button>
          <Button variant="contained" onClick={handleProceedToEditor}>Create</Button>
        </DialogActions>
      </Dialog>

      {messages.map(m => (
        <Alert key={m.id} severity={m.type} sx={{ position: 'fixed', bottom: 20, right: 20, minWidth: 250 }}>
          {m.msg}
        </Alert>
      ))}
    </Box>
  );
}

// End of Workflow components

export default Workflow;
