import React from 'react';
import { Box, Paper, Typography, Grid, Card, CardContent } from '@mui/material';
import {
  Construction as ConstructionIcon,
  TrendingUp as TrendingUpIcon,
  Schedule as ScheduleIcon,
  CheckCircle as CheckCircleIcon,
  Error as ErrorIcon
} from '@mui/icons-material';

// Use Vite's environment check for development mode
const DEV_MODE = import.meta.env.DEV;

function Analytics() {
  // Show under development message if not in dev mode
  if (!DEV_MODE) {
    return (
      <Box
        sx={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          height: '100%',
          p: 4,
          textAlign: 'center'
        }}
      >
        <ConstructionIcon sx={{ fontSize: 80, color: 'warning.main', mb: 2 }} />
        <Typography variant="h4" gutterBottom fontWeight="bold">
          Analytics
        </Typography>
        <Typography variant="h6" color="text.secondary" gutterBottom>
          🚧 Under Development 🚧
        </Typography>
        <Paper
          elevation={0}
          sx={{
            p: 3,
            mt: 2,
            maxWidth: 500,
            bgcolor: 'warning.light',
            borderRadius: 2,
            border: '1px solid',
            borderColor: 'warning.main'
          }}
        >
          <Typography variant="body1" color="text.primary">
            This feature is currently being developed. It will provide insights and analytics about your workflow runs and agent performance.
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 2 }}>
            In the meantime, you can view workflow run history on the <strong>Dashboard</strong> page.
          </Typography>
        </Paper>
      </Box>
    );
  }

  // Development mode - show analytics placeholder
  return (
    <Box sx={{ p: 3 }}>
      <Box sx={{ mb: 4 }}>
        <Typography variant="h4" sx={{ fontWeight: 700, mb: 1 }}>Analytics</Typography>
        <Typography variant="body1" color="text.secondary">Workflow and agent performance metrics (Development Preview)</Typography>
      </Box>

      <Grid container spacing={3}>
        {/* Summary Cards */}
        <Grid item xs={12} sm={6} md={3}>
          <Card>
            <CardContent>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
                <ScheduleIcon color="primary" />
                <Typography variant="subtitle2" color="text.secondary">
                  Total Runs
                </Typography>
              </Box>
              <Typography variant="h4">--</Typography>
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={12} sm={6} md={3}>
          <Card>
            <CardContent>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
                <CheckCircleIcon color="success" />
                <Typography variant="subtitle2" color="text.secondary">
                  Success Rate
                </Typography>
              </Box>
              <Typography variant="h4">--%</Typography>
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={12} sm={6} md={3}>
          <Card>
            <CardContent>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
                <ErrorIcon color="error" />
                <Typography variant="subtitle2" color="text.secondary">
                  Failed Runs
                </Typography>
              </Box>
              <Typography variant="h4">--</Typography>
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={12} sm={6} md={3}>
          <Card>
            <CardContent>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
                <TrendingUpIcon color="info" />
                <Typography variant="subtitle2" color="text.secondary">
                  Avg Duration
                </Typography>
              </Box>
              <Typography variant="h4">--s</Typography>
            </CardContent>
          </Card>
        </Grid>

        {/* Placeholder for charts */}
        <Grid item xs={12} md={8}>
          <Paper sx={{ p: 3, height: 300, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <Box sx={{ textAlign: 'center' }}>
              <TrendingUpIcon sx={{ fontSize: 48, color: 'text.disabled', mb: 1 }} />
              <Typography variant="body1" color="text.secondary">
                Workflow runs chart coming soon
              </Typography>
            </Box>
          </Paper>
        </Grid>
        <Grid item xs={12} md={4}>
          <Paper sx={{ p: 3, height: 300, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <Box sx={{ textAlign: 'center' }}>
              <CheckCircleIcon sx={{ fontSize: 48, color: 'text.disabled', mb: 1 }} />
              <Typography variant="body1" color="text.secondary">
                Success/failure breakdown coming soon
              </Typography>
            </Box>
          </Paper>
        </Grid>
      </Grid>
    </Box>
  );
}

export default Analytics;
