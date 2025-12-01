import React from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import {
  Drawer,
  List,
  ListItem,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Divider,
  IconButton,
  Typography,
  Box,
  useTheme,
  useMediaQuery
} from '@mui/material';
import {
  ChevronLeft,
  Menu,
} from '@mui/icons-material';
import { useSidebar } from '../../context/SidebarContext';
import { navigationItems } from './navigationItems';

export default function Sidebar() {
  const navigate = useNavigate();
  const location = useLocation();
  const theme = useTheme();
  const isMobile = useMediaQuery(theme.breakpoints.down('md'));
  const { isOpen, isMobileOpen, toggleSidebar, toggleMobileSidebar, closeMobileSidebar } = useSidebar();
  const isElectron = window.electronAPI ? true : false;

  const handleNavigation = (path) => {
    navigate(path);
    if (isMobile) {
      closeMobileSidebar();
    }
  };

  const isActive = (path) => {
    return location.pathname === path;
  };

  const drawerContent = (
    <Box sx={{ 
      height: '100%', 
      display: 'flex', 
      flexDirection: 'column',
      width: isOpen ? 270 : 64,
      transition: 'width 225ms cubic-bezier(0.4, 0, 0.6, 1) 0ms',
    }}>
      {/* Header */}
      <Box sx={{ p: 3, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        {isOpen ? (
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, width: '100%', justifyContent: 'space-between' }}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
              <img 
                src="./favicon.ico" 
                alt="OnCall Agent Logo" 
                width={28} 
                height={28}
                style={{ display: 'block' }}
              />
              <Typography variant="h6" sx={{ fontWeight: 'bold', color: 'text.primary', fontSize: '1.1rem' }}>
                OnCall Agent
              </Typography>
            </Box>
            {!isMobile && (
              <IconButton onClick={toggleSidebar} size="small" sx={{ ml: 1 }}>
                <ChevronLeft sx={{ transform: isOpen ? 'rotate(0deg)' : 'rotate(180deg)', transition: 'transform 0.2s' }} />
              </IconButton>
            )}
          </Box>
        ) : (
          <Box 
            sx={{ 
              display: 'flex', 
              alignItems: 'center', 
              justifyContent: 'center', 
              width: '100%',
              cursor: 'pointer',
              borderRadius: 2,
              p: 1.5,
              '&:hover': {
                backgroundColor: 'action.hover'
              }
            }}
            onClick={toggleSidebar}
          >
            <img 
              src="./favicon.ico" 
              alt="OnCall Agent Logo" 
              width={24} 
              height={24}
              style={{ display: 'block' }}
            />
          </Box>
        )}
      </Box>
      
      <Divider />
      
      {/* Navigation */}
      <List sx={{ flexGrow: 1, px: 2, py: 1 }}>
        {navigationItems.map((item) => {
          const Icon = item.icon;
          const active = isActive(item.path);
          
          return (
            <ListItem key={item.id} disablePadding sx={{ mb: 1 }}>
              <ListItemButton
                onClick={() => handleNavigation(item.path)}
                sx={{
                  borderRadius: 2,
                  backgroundColor: active ? 'error.main' : 'transparent',
                  color: active ? 'error.contrastText' : 'text.primary',
                  justifyContent: isOpen ? 'flex-start' : 'center',
                  px: isOpen ? 3 : 2,
                  py: 1.5,
                  minHeight: 48,
                  '&:hover': {
                    backgroundColor: active ? 'error.dark' : 'action.hover',
                  }
                }}
              >
                <ListItemIcon sx={{ 
                  color: 'inherit',
                  minWidth: isOpen ? 48 : 'auto',
                  justifyContent: 'center'
                }}>
                  <Icon sx={{ fontSize: '1.25rem' }} />
                </ListItemIcon>
                {isOpen && (
                  <ListItemText 
                    primary={item.title}
                    sx={{
                      ml: 1,
                      '& .MuiListItemText-primary': {
                        fontSize: '0.9rem',
                        fontWeight: active ? 600 : 500,
                        lineHeight: 1.2
                      }
                    }}
                  />
                )}
              </ListItemButton>
            </ListItem>
          );
        })}
      </List>
      
      <Divider />
      
      {/* Footer */}
      {isOpen && (
        <Box sx={{ p: 2 }}>
          <Typography variant="caption" color="text.secondary">
            OnCall Management System v1.0
          </Typography>
        </Box>
      )}
    </Box>
  );

  if (isMobile) {
    return (
      <>
        {/* Mobile Menu Button */}
        <IconButton
          onClick={toggleMobileSidebar}
          sx={{
            position: 'fixed',
            top: 16,
            left: 16,
            zIndex: theme.zIndex.drawer + 1,
            backgroundColor: 'background.paper',
            boxShadow: 2,
            '&:hover': {
              backgroundColor: 'action.hover',
            }
          }}
        >
          <Menu />
        </IconButton>
        
        {/* Mobile Drawer */}
        <Drawer
          variant="temporary"
          open={isMobileOpen}
          onClose={closeMobileSidebar}
          ModalProps={{
            keepMounted: true,
          }}
          sx={{
            '& .MuiDrawer-paper': {
              width: 270,
              boxSizing: 'border-box',
              position: 'fixed',
              height: '100vh',
              top: 0,
              left: 0,
            },
          }}
        >
          {drawerContent}
        </Drawer>
      </>
    );
  }

  return (
    <Box
      sx={{
        width: isOpen ? 270 : 64,
        flexShrink: 0,
        position: 'fixed',
        height: '100vh',
        top: 0,
        left: 0,
        zIndex: theme.zIndex.drawer,
        backgroundColor: 'background.paper',
        borderRight: '1px solid',
        borderColor: 'divider',
        transition: 'width 225ms cubic-bezier(0.4, 0, 0.6, 1) 0ms',
        overflow: 'hidden',
      }}
    >
      {drawerContent}
    </Box>
  );
}