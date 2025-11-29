import React from 'react';

const Navbar = ({ currentView, onViewChange }) => {
  const navItems = [
    { id: 'dashboard', label: 'Dashboard', icon: '📊' },
    { id: 'scheduler', label: 'Scheduler', icon: '📅' },
    { id: 'oncall', label: 'On-Call', icon: '🚨' },
    { id: 'settings', label: 'Settings', icon: '⚙️' }
  ];

  return (
    <nav className="navbar">
      <div className="navbar-brand">
        <h1 className="navbar-title">OnCall Agent</h1>
      </div>
      
      <div className="navbar-nav">
        {navItems.map((item) => (
          <button
            key={item.id}
            className={`nav-item ${currentView === item.id ? 'active' : ''}`}
            onClick={() => onViewChange && onViewChange(item.id)}
          >
            <span className="nav-icon">{item.icon}</span>
            <span className="nav-label">{item.label}</span>
          </button>
        ))}
      </div>
      
      <div className="navbar-user">
        <div className="user-info">
          <span className="user-name">Admin</span>
          <span className="user-status">Online</span>
        </div>
      </div>
    </nav>
  );
};

export default Navbar;