from app.modules.copilot.graph.graph import Graph

# Global instance that will be initialized during app startup
agent = None


def get_agent() -> Graph:
    """Get the global agent instance"""
    global agent
    if agent is None:
        raise ValueError("Agent not initialized")
    return agent


def init_copilot():
    """Initialize the global Agent instance"""
    global agent
    agent = Graph()
    return agent
