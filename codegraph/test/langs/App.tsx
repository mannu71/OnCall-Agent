import React from 'react';
export const Button = ({ label }) => {
  return <button>{label}</button>;
};
const handleClick = () => { doThing(); };
function doThing() { return 1; }
