import React from 'react';
import { Box, Paper, Typography } from '@mui/material';
import { Construction as ConstructionIcon } from '@mui/icons-material';

function Analytics() {
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

export default Analytics;
