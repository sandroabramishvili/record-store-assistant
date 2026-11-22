"""The state's per-turn reset, shown on a tiny real LangGraph graph with a checkpointer."""

import json
from typing import get_type_hints

from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from app.ai.state import PERSISTENT_FIELDS, TURN_RESET, StoreState, new_turn_input


def test_every_field_is_either_reset_or_deliberately_persistent():
    """Guards the future: a new state field must be added to TURN_RESET or PERSISTENT_FIELDS."""
    fields = set(get_type_hints(StoreState))
    assert fields == set(TURN_RESET) | PERSISTENT_FIELDS
    assert not set(TURN_RESET) & PERSISTENT_FIELDS
    assert 'shown_item_ids' not in TURN_RESET                 # "the second one" needs it next turn


def test_turn_input_is_json_safe_except_messages():
    turn = new_turn_input('hi', 7)
    json.dumps({k: v for k, v in turn.items() if k != 'messages'})
    assert turn['user_id'] == 7 and turn['relax_attempts'] == 0


def build_toy_graph():
    """One node that behaves like a turn of the real graph: relaxes once, shows two copies."""
    def turn(state: StoreState) -> dict:
        return {'relax_attempts': state.get('relax_attempts', 0) + 1,
                'shown_item_ids': [57, 58],
                'messages': [AIMessage(content='1. Head Hunters\n2. Black Byrd')]}
    g = StateGraph(StoreState)
    g.add_node('turn', turn)
    g.add_edge(START, 'turn')
    g.add_edge('turn', END)
    return g.compile(checkpointer=InMemorySaver())


async def test_reset_between_turns_and_what_survives():
    graph = build_toy_graph()
    config = {'configurable': {'thread_id': 'chat-1'}}

    await graph.ainvoke(new_turn_input('70s jazz-funk?', 1), config)
    state = await graph.ainvoke(new_turn_input('the second one please', 1), config)

    assert state['relax_attempts'] == 1               # reset to 0, then this turn's +1 (not 2)
    assert state['shown_item_ids'] == [57, 58]         # survived into the second turn
    assert [m.type for m in state['messages']] == ['human', 'ai', 'human', 'ai']   # appended, not replaced


async def test_without_reset_the_counter_leaks_into_the_next_turn():
    """Why the reset exists: send only the message, and last turn's counter carries over."""
    graph = build_toy_graph()
    config = {'configurable': {'thread_id': 'chat-2'}}
    await graph.ainvoke(new_turn_input('x', 1), config)
    state = await graph.ainvoke({'messages': [('human', 'y')]}, config)   # no reset values
    assert state['relax_attempts'] == 2                # leaked: the new search would stop relaxing too early


async def test_separate_chats_have_separate_state():
    graph = build_toy_graph()
    await graph.ainvoke(new_turn_input('a', 1), {'configurable': {'thread_id': 'chat-a'}})
    other = await graph.ainvoke(new_turn_input('b', 2), {'configurable': {'thread_id': 'chat-b'}})
    assert len(other['messages']) == 2                 # chat-b doesn't see chat-a's messages
