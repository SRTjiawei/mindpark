import os
from threading import Lock
import numpy as np
from vizbot.core import GymEnv, StopEpisode
from vizbot.utility import Experience, ensure_directory


class StopTraining(Exception):

    def __init__(self, trainer):
        super().__init__(self)
        self.trainer = trainer


class Trainer:

    def __init__(self, directory, env_name, timesteps, epoch_size=None,
                 videos=0, experience=False, experience_maxlen=5e4):
        """
        Provide an interface to agents to train on an environment.

        Args:
            directory (path): Where to store results. Can be None for dry run.
            env_name (str): The name of a registered Gym environment.
            timesteps (int): The overall training duration in frames.
            epoch_size (int): Every how many frames to print statistics.
                Defaults to timesteps / 100.
            videos (int): Every how many episodes to record a video. Zero
                disables recording.
            experience (bool): Whether to store transition tuples as Numpy
                arrays. Requires a lot of disk space.
            experience_maxlen (int): Maxmium amount of transitions to store per
                episode. Usually much higher than the normal episode length.
                Needed to allocate memory beforehand.
        """
        if directory:
            ensure_directory(directory)
        self._directory = directory
        self._env_name = env_name
        self._timesteps = timesteps
        self._epoch_size = epoch_size or timesteps // 100
        self._videos = videos
        self._experience = experience
        self._experience_maxlen = experience_maxlen
        self._envs = []
        self._preprocesses = []
        self._scores = []
        self._epoch = 0
        self._episode = 0
        self._timestep = 0
        self._states = None
        self._actions = None
        self._running = True
        self._lock = Lock()

    @property
    def running(self):
        return self._running

    @property
    def episode(self):
        return self._episode

    @property
    def timestep(self):
        return self._timestep

    @property
    def scores(self):
        return self._scores

    def add_preprocess(self, preprocess_cls, *args, **kwargs):
        if self._envs:
            raise RuntimeError('must add preprocesses before creating envs')
        self._preprocesses.append((preprocess_cls, args, kwargs))

    def create_env(self):
        env = GymEnv(self._env_name, self._directory, self._video_callback)
        for preprocess, args, kwargs in self._preprocesses:
            env = preprocess(env, *args, **kwargs)
            self._envs.append(env)
        return env

    def run_episode(self, agent, env):
        if not self._running:
            raise StopTraining(self)
        with self._lock:
            episode = self._episode
            self._episode += 1
        with env.lock:
            score = self._run_episode(env, agent, episode)
        self._scores.append(score)
        with self._lock:
            epoch_end = (self._epoch + 1) * self._epoch_size
            if self._running and self._timestep >= epoch_end:
                self._next_epoch()
            if self._timestep >= self._timesteps:
                self._stop_training()

    def _run_episode(self, env, agent, episode):
        if self._directory and self._experience:
            experience = Experience(self._experience_maxlen)
        score = 0
        agent.start()
        try:
            state = env.reset()
            while True:
                action = agent.step(state)
                successor, reward = env.step(action)
                transition = (state, action, reward, successor)
                agent.experience(*transition)
                if self._directory and self._experience:
                    experience.append(transition)
                state = successor
                score += reward
                self._timestep += 1
        except StopEpisode:
            pass
        agent.stop()
        if self._directory and self._experience:
            experience.save(os.path.join(
                self._directory, 'experience-{}.npz'.format(episode)))
        return score

    def _next_epoch(self):
        self._epoch += 1
        message = 'Epoch {} timestep {} average score {}'
        score = self._scores[-self._epoch_size:]
        score = sum(score) / len(score)
        print(message.format(self._epoch, self.timestep, score))

    def _stop_training(self):
        self._running = False
        for env in self._envs:
            with env.lock:
                env.close()
        self._store_scores()

    def _store_scores(self):
        if self._directory:
            scores = np.array(self._scores)
            np.save(os.path.join(self._directory, 'scores.npy'), scores)

    def _video_callback(self, env_episode):
        if not self._videos:
            return False
        return self.episode % self._videos == 0
