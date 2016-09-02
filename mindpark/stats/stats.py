import os
import collections
import functools
import matplotlib.pyplot as plt
from mpl_toolkits.axes_grid1 import make_axes_locatable
from matplotlib.ticker import MaxNLocator
import numpy as np
import sqlalchemy as sql
# from mindpark.plot.scatter import Scatter
from mindpark.utility import get_subdirs, natural_sorted, read_yaml


Run = collections.namedtuple(
    'Run', 'experiment name env algorithm repeat stats')


class Metrics:

    def __init__(self, type_, metrics=None):
        self._type = type_
        self._metrics = metrics

    def __call__(self, experiment):
        for run in self._collect_runs(experiment):
            self._process(run)

    def _collect_runs(self, experiment):
        name = os.path.basename(experiment).title()
        for env_dir in get_subdirs(experiment):
            env = os.path.basename(env_dir)
            for directory in natural_sorted(get_subdirs(env_dir)):
                repeat = int(directory.rsplit('-', 1)[-1])
                algorithm = read_yaml(directory, 'algorithm.yaml').name
                stats = os.path.join(directory, 'stats.db')
                yield Run(experiment, name, env, algorithm, repeat, stats)

    def _process(self, run):
        title = '{} on {} (Repeat {})'.format(
            run.algorithm, run.env, run.repeat)
        filepath = '{}-{}-{}-{}.{}'.format(
            run.name, run.env, run.algorithm, run.repeat, self._type)
        filepath = os.path.join(run.experiment, filepath.lower())
        self._figure(run.stats, title, filepath)

    def _figure(self, stats, title, filepath):
        engine = sql.create_engine('sqlite:///{}'.format(stats))
        tables = self._get_tables(engine)
        tables = self._select_metrics(tables)
        fig, ax = self._subplots(2, len(tables))
        fig.suptitle(title, fontsize=16)
        for index, (title, table) in enumerate(tables):
            rows = self._collect_stats(engine, table)
            if rows is None:
                continue
            test, train = rows[rows.T[4] == 0], rows[rows.T[4] == 1]
            if train.size:
                self._plot(ax[0, index], train)
                ax[0, index].set_title(title)
            else:
                ax[0, index].set_axis_off()
            if test.size:
                self._plot(ax[1, index], test)
                ax[1, index].set_title(title)
            else:
                ax[1, index].set_axis_off()
        ax[0, 0].set_ylabel('Training', fontsize=16)
        ax[0, 0].yaxis.labelpad = 16
        ax[1, 0].set_ylabel('Testing', fontsize=16)
        ax[1, 0].yaxis.labelpad = 16
        fig.tight_layout(rect=[0, 0, 1, .94])
        fig.savefig(filepath)

    def _select_metrics(self, tables):
        if not self._metrics:
            return natural_sorted(tables.items(), key=lambda x: x[0])
        selected = []
        for metric in self._metrics:
            matches = [x for x in tables.keys() if metric in x]
            if not matches:
                raise KeyError("found no metric for '{}'".format(metric))
            if len(matches) > 1:
                message = "found multiple metric for '{}'".format(metric)
                raise KeyError(message)
            selected.append((matches[0], tables[matches[0]]))
        return selected

    def _collect_stats(self, engine, table):
        result = engine.execute(sql.select([table]))
        columns = np.array([x for x in result]).T
        if not len(columns) or not columns.shape[1]:
            return None
        id_, timestamp, step, epoch, training, episode = columns[:6]
        id_ = [int(x, 16) for x in id_]
        order = np.lexsort([id_, step, episode, training, epoch])
        rows = columns.T
        rows = rows[order]
        return rows

    def _plot(self, ax, rows):
        _, _, _, epoch, training, _ = rows.T[:6]
        values = rows[:, 6:].astype(float)
        categorical = np.allclose(values, values.astype(int))
        resolution = 10
        borders = np.linspace(0, len(values), resolution * epoch.max())
        borders = borders.astype(int)
        if values.shape[1] == 1 and not categorical:
            value = values[:, 0]
            domain = np.linspace(0, epoch.max() + 1, len(values))
            ax.scatter(domain, value, c=training, alpha=0.1, lw=0)
            ax.set_xlim(domain.min(), domain.max())
            padding = 0.05 * (value.max() - value.min())
            padding = padding or np.abs(np.log10(value[0])) / 100
            ax.set_ylim(value.min() - padding, value.max() + padding)
        elif values.shape[1] == 1 and categorical:
            value = values[:, 0].astype(int)
            reducer = functools.partial(np.bincount, minlength=value.max() + 1)
            groups = self._aggregate(value, borders, reducer)
            groups = groups / groups.sum(1)[:, np.newaxis]
            domain = np.linspace(0, epoch.max() + 1, resolution * len(groups))
            bar = self._plot_color_grid(ax, domain, groups)
            bar.set_ticks([])
        elif values.shape[1] > 1:
            reducer = functools.partial(np.mean, axis=0)
            groups = self._aggregate(values, borders, reducer)
            domain = np.linspace(0, epoch.max() + 1, resolution * len(groups))
            self._plot_color_grid(ax, domain, groups)
        ax.xaxis.set_major_locator(MaxNLocator(integer=True))

    def _plot_color_grid(self, ax, domain, cells):
        extent = [domain.min(), domain.max(), -.5, cells.shape[1] - .5]
        kwargs = dict(cmap='viridis')
        img = ax.matshow(
            cells.T, extent=extent, origin='lower', aspect='auto', **kwargs)
        divider = make_axes_locatable(ax)
        cax = divider.append_axes('right', size='7%', pad=0.1)
        bar = plt.colorbar(img, cax=cax)
        ax.xaxis.set_ticks_position('bottom')
        return bar

    def _subplots(self, rows, cols, **kwargs):
        size = [4 * cols, 3 * rows]
        fig, ax = plt.subplots(ncols=cols, nrows=rows, figsize=size, **kwargs)
        if cols == 1:
            ax = np.array([ax]).T
        return fig, ax

    def _get_tables(self, engine):
        metadata = sql.MetaData()
        metadata.reflect(engine)
        return metadata.tables

    def _aggrerate_consecutive(self, values, keys, reducer):
        if not isinstance(keys, np.ndarray):
            keys = np.stack(keys, 1)
        assert len(values) == len(keys)
        changes = np.diff(keys, axis=0)
        changes = np.abs(changes).max(1)
        borders = np.where(changes > 0)[0]
        borders = np.array([0] + borders.tolist() + [-1], dtype=int)
        domain = keys[borders]
        groups = self._aggregate(values, borders, reducer)
        return domain, groups

    def _aggregate(self, values, borders, reducer):
        groups = []
        for start, stop in zip(borders[:-1], borders[1:]):
            groups.append(reducer(values[start: stop]))
        groups = np.array(groups)
        return groups