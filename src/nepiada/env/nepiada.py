import functools
import numpy as np

import gymnasium
from gymnasium.spaces import Discrete, Box

from pettingzoo import ParallelEnv
from pettingzoo.utils import parallel_to_aec, aec_to_parallel, wrappers

from utils.config import Config
from utils.world import World


def parallel_env(config: Config):
    """
    The env function often wraps the environment in wrappers by default.
    Converts to AEC API then back to Parallel API since the wrappers are
    only supported in AEC environments.
    """
    internal_render_mode = "human"
    env = raw_env(render_mode=internal_render_mode, config=config)
    # this wrapper helps error handling for discrete action spaces
    env = wrappers.AssertOutOfBoundsWrapper(env)
    # Provides a wide vareity of helpful user errors
    env = wrappers.OrderEnforcingWrapper(env)
    env = aec_to_parallel(env)
    return env


def raw_env(render_mode=None, config: Config = Config()):
    """
    To support the AEC API, the raw_env() function just uses the from_parallel
    function to convert from a ParallelEnv to an AEC env
    """
    env = nepiada(render_mode=render_mode, config=config)
    env = parallel_to_aec(env)
    return env


class nepiada(ParallelEnv):
    metadata = {"render_modes": ["human"], "name": "nepiada_v1"}

    def __init__(self, render_mode=None, config: Config = Config()):
        """
        The init method takes in environment arguments and should define the following attributes:
        - possible_agents
        - render_mode

        These attributes should not be changed after initialization.
        """

        self.total_agents = config.num_good_agents + config.num_adversarial_agents
        self.config = config
        self.possible_agents = []

        self.render_mode = render_mode

        # Initializing agents and grid
        self.world = World(config)

        # Add IDs to possible agents
        for id in self.world.agents:
            self.possible_agents.append(id)

    # lru_cache allows observation and action spaces to be memoized, reducing clock cycles required to get each agent's space.
    @functools.lru_cache(maxsize=None)
    def observation_space(self, agent):
        # gymnasium spaces are defined and documented here: https://gymnasium.farama.org/api/spaces/

        # Observation space is defined as a N x 2 matrix, where each row corresponds to an agents coordinates.
        # The first column stores the x coordinate and the second column stores the y coordinate
        return Box(
            low=0, high=self.config.size, shape=(self.total_agents, 2), dtype=np.int_
        )

    # Action space should be defined here.
    @functools.lru_cache(maxsize=None)
    def action_space(self, agent):
        # Discrete movement, either up, down, stay, left or right.
        return Discrete(5)

    def render(self):
        """
        Renders the environment. In human mode, it can print to terminal, open
        up a graphical window, or open up some other display that a human can see and understand.
        """
        if self.render_mode is None:
            gymnasium.logger.warn(
                "You are calling render method without specifying any render mode."
            )
            return
        elif self.render_mode == "human":
            # Temporary to print grid for debug purposes until we have a better way to render.
            self.world.grid.render_grid()
            self.world.graph.render_graph(comm=False)
            return

    def observe(self, agent_name):
        """
        Observe should return the agents within the observation radius of the specified agent.
        Param: Unique agent string identifier: e.g. 'adversarial_0'
        Return: An array of agents who's position it can directly observe
        """
        return np.array(self.world.graph.obs[agent_name])

    def close(self):
        """
        Close should release any graphical displays, subprocesses, network connections
        or any other environment data which should not be kept around after the
        user is no longer using the environment.
        """
        pass

    def reset(self, seed=None, options=None):
        """
        Reset needs to initialize the `agents` attribute and must set up the
        environment so that render(), and step() can be called without issues.
        Here it initializes the `num_moves` variable which counts the number of
        steps taken in the environment.
        Returns the observations for each agent
        """
        self.agents = self.possible_agents[:]
        print("All Agents: ", str(self.agents))

        self.num_moves = 0

        # Reinitialize the grid
        self.world.grid.reset_grid()

        # Reset the comm and observation graphs
        self.world.graph.reset_graphs()

        # Reset the rewards
        self.rewards = {agent: 0 for agent in self.agents}

        # Reset the truncations
        self.truncations = {agent: False for agent in self.agents}

        # Info will be used to pass information about comm and obs graphs
        self.infos = {agent: {} for agent in self.agents}

        # The observation returned below is the position of each agent on the grid at the moment
        self.observations = {agent: None for agent in self.agents}
        for agent_name in self.agents:
            agent = self.world.get_agent(agent_name)
            self.observations[agent_name] = [agent.p_pos[0], agent.p_pos[1]] 

        return self.observations, self.infos

    def step(self, actions):
        """
        step(action) takes in an action for each agent and should return the
        - observations
            Returns the current state of the grid

        - rewards
            A 1xN reward vector where agent's uid corresponds to its reward index

        - terminations
            A 1xN vector where each index corresponds to agent's uid.
            True if a particular agent's episode has terminated, false otherwise

        - truncations
            #TODO

        - infos
            Is a dictionary with agent_names as the key. Each value in turn is a dict
            of the form {"obs": [], "comm": []}
            To access a agent's observation graph: infos[agent_name]["obs"]
            To access a agent's observation graph: infos[agent_name]["comm"]

        """
        # Assert that number of actions are equal to the number of agents
        assert len(actions) == len(self.agents)

        # Update drone positions
        self.move_drones(actions)

        # We should update the rewards here, for now, we will just set everything to 0
        rewards = {agent: 0 for agent in self.agents}

        terminations = {agent: False for agent in self.agents}
        self.num_moves += 1
        env_truncation = self.num_moves >= Config.iterations
        truncations = {agent: env_truncation for agent in self.agents}

        # Update the observation and communication graphs at each iteration
        # Note: Currently only the observation graph will be updated as it uses a dynamic observation radius concept
        #       The communication graph remains static as per the environment specifications
        self.world.update_graphs()

        # The observation returned in 'step' function is the current position of all agents
        # Note this is different from observation graph!
        self.observations = {agent: None for agent in self.agents}
        for agent_name in self.agents:
            agent = self.world.get_agent(agent_name)
            self.observations[agent_name] = [agent.p_pos[0], agent.p_pos[1]] 

        # Info will be used to pass information about comm and obs graphs
        self.infos = {agent_name: {} for agent_name in self.agents}
        for agent_name in self.agents:
            self.infos[agent_name]["obs"] = self.world.graph.obs[agent_name]
            self.infos[agent_name]["comm"] = self.world.graph.comm[agent_name]

        if self.render_mode == "human":
            self.render()

        return self.observations, rewards, terminations, truncations, self.infos

    def move_drones(self, actions):
        """
        Move the drones according to the actions
        """
        # Drones that collided with another drone, for reprocessing
        collided_drones = []

        for agent_id in self.agents:
            agent = self.world.agents[agent_id]
            action = actions[agent_id]
            status = self.world.grid.move_drone(agent, action)
            if status == -2:
                collided_drones.append(agent_id)
            elif status == -1:
                # Drone collided with boundary
                pass

        # Check collided drones in reverse to see if moving them is possible in this step
        for agent_id in reversed(collided_drones):
            agent = self.world.agents[agent_id]
            action = actions[agent_id]
            status = self.world.grid.move_drone(agent, action)
            if status == -2:
                # Drone collided with another drone
                pass

