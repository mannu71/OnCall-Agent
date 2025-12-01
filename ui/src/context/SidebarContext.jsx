import React, { createContext, useContext, useState, useCallback, useMemo } from 'react';

const SidebarContext = createContext();

export const useSidebar = () => {
  const context = useContext(SidebarContext);
  if (!context) {
    throw new Error('useSidebar must be used within a SidebarProvider');
  }
  return context;
};

export const SidebarProvider = ({ children }) => {
  const [isOpen, setIsOpen] = useState(true);
  const [isMobileOpen, setIsMobileOpen] = useState(false);

  const toggleSidebar = useCallback(() => {
    setIsOpen(prev => !prev);
  }, []);

  const toggleMobileSidebar = useCallback(() => {
    setIsMobileOpen(prev => !prev);
  }, []);

  const closeMobileSidebar = useCallback(() => {
    setIsMobileOpen(false);
  }, []);

  const value = useMemo(() => ({
    isOpen,
    isMobileOpen,
    toggleSidebar,
    toggleMobileSidebar,
    closeMobileSidebar,
  }), [isOpen, isMobileOpen, toggleSidebar, toggleMobileSidebar, closeMobileSidebar]);

  return (
    <SidebarContext.Provider value={value}>
      {children}
    </SidebarContext.Provider>
  );
};