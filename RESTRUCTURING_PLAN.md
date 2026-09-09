# App.tsx Restructuring Plan

## Overview
This document outlines the changes needed to restructure App.tsx to:
1. Lift bootstrap data loading to application level
2. Implement conversation switching between views
3. Provide shared state access for TopBar and ChatPage components

## Changes Required

### 1. Bootstrap Data Loading
- Move user and settings loading from individual components to App.tsx
- Load conversation data (sessions) at app initialization
- Create global state for application context

### 2. Conversation Management
- Implement session management system in App.tsx
- Create signals for conversations list and active conversation
- Add functionality to switch between conversations  
- Provide conversation data to TopBar component

### 3. Component Integration
- Modify TopBar to accept conversations list as prop
- Modify ChatPage to receive active conversation data as prop
- Handle view switching logic at app level

## Implementation Details

### App.tsx Changes:
1. Import `createSignal` from solid-js
2. Add state management for:
   - Conversations list (`[Session[], setConversations]`)
   - Active conversation (`[Session | null, setActiveConversation]`) 
3. Fetch user, settings and sessions data on app init
4. Pass conversations to TopBar and active conversation to ChatPage

### TopBar Component Changes:
1. Accept `conversations` prop (array of session objects)  
2. Accept `onConversationChange` prop (callback function)
3. Implement dropdown change handling that calls the callback
4. Add new conversation button functionality

### ChatPage Component Changes:
1. Accept `conversation` prop (current session object)
2. Use conversation data to display proper context
3. Update rendering based on active conversation state

## Technical Approach

1. **State Management**: Use SolidJS signals for reactive application state  
2. **Data Flow**: Lift shared data to App level, pass down to components as props
3. **Event Handling**: Create callback functions in App.tsx for component interactions
4. **API Integration**: Add missing `fetchSessions` function if needed

## Considerations

- Maintain existing styling and component structure
- Ensure backward compatibility with existing APIs
- Handle loading states properly
- Follow existing code patterns and conventions