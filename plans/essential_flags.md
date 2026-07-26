# Plan: Essential Flags Implementation

## Overview
Implement essential flags in the cognitive agent's memory system to distinguish between important information that should be preserved indefinitely versus non-essential information. This allows the system to maintain all learned information while having the capability to mark critical items for preservation.

## Status
- [ ] All tasks completed

## Tasks

### Task 1: Update Database Schema
**File**: `/Users/michaelbosworth/Projects/personal/mydigitalassistant.ai/assistant/backend/db/schema.py`

Add `essential INTEGER NOT NULL DEFAULT 0` column to:
- [ ] frames table (after confidence column)
- [ ] slots table (after confidence column)
- [ ] associations table (after confidence column)

### Task 2: Update Memory Models
**File**: `/Users/michaelbosworth/Projects/personal/mydigitalassistant.ai/assistant/backend/memory/models.py`

Add `essential: int = 0` field to:
- [ ] Frame class (after confidence field)
- [ ] Slot class (after confidence field)
- [ ] Association class (after confidence field)

### Task 3: Update Persistence Layer
**File**: `/Users/michaelbosworth/Projects/personal/mydigitalassistant.ai/assistant/backend/memory/store.py`

Update methods to handle essential flags:
- [ ] `create_frame()` - accept `essential: int = 0` parameter and include in INSERT
- [ ] `upsert_slot()` - accept `essential: int = 0` parameter and include in INSERT/UPDATE
- [ ] `create_association()` - accept `essential: int = 0` parameter and include in INSERT
- [ ] Update all retrieval methods to SELECT essential column and include in results
- [ ] Modify conflict resolution to preserve essential flag when resolving conflicts

### Task 4: Test Implementation
- [ ] Verify essential flags work correctly in all scenarios
- [ ] Confirm backward compatibility with existing functionality
- [ ] Test that essential information is preserved through operations
- [ ] Run existing tests to ensure no regressions

## Decisions
- Essential flag is an integer (0 = non-essential, 1 = essential)
- Default value is 0 (non-essential) for all new items
- All information is remembered initially - no forgetting mechanism
- Essential flag allows future implementation of selective forgetting if needed
- Search results are always saved initially (no toggle needed)

## Files to Modify
1. [ ] `/Users/michaelbosworth/Projects/personal/mydigitalassistant.ai/assistant/backend/db/schema.py`
2. [ ] `/Users/michaelbosworth/Projects/personal/mydigitalassistant.ai/assistant/backend/memory/models.py`
3. [ ] `/Users/michaelbosworth/Projects/personal/mydigitalassistant.ai/assistant/backend/memory/store.py`

## Verification Criteria
- [ ] Essential flags are correctly set when creating new memory items
- [ ] All existing functionality remains intact after implementation
- [ ] Essential flag persists through memory operations
- [ ] Non-essential items can be identified for potential future forgetting
- [ ] Essential information is preserved in all operations

## Notes
- Implementation should be minimal and focused
- No forgetting mechanism needed - all information is remembered
- Essential flag is purely for future selective retention if needed
- Default behavior: all search results are saved initially