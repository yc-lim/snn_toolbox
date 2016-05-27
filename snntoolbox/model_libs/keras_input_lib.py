# -*- coding: utf-8 -*-
"""
Methods to parse an input model and prepare it for further processing in the
SNN toolbox.

The idea is to make all further steps in the conversion/simulation pipeline
independent of the original model format. Therefore, when a developer adds a
new input model library (e.g. Caffe) to the toolbox, the following methods must
be implemented and satisfy the return requirements specified in their
respective docstrings:

    - extract
    - evaluate
    - load_ann

Created on Thu May 19 08:21:05 2016

@author: rbodo
"""

import os
import theano
from keras import backend as K
from snntoolbox.config import settings, bn_layers
from snntoolbox.model_libs.common import absorb_bn, import_script


def extract(model):
    """
    Extract the essential information about a neural network.

    This method serves to abstract the conversion process of a network from the
    language the input model was built in (e.g. Keras or Lasagne).

    To extend the toolbox by another input format (e.g. Caffe), this method has
    to be implemented for the respective model library.

    Attributes
    ----------

        - weights : array
            Weights connecting the input layer.

        - biases : array
            Biases of the network. For conversion to spiking nets, zero biases
            are found to work best.

        - input_shape : list
            The dimensions of the input sample.

        - layers : list
            List of all the layers of the network, where each layer contains a
            dictionary with keys

            - layer_num : int
                Index of layer.

            - layer_type : string
                Describing the type, e.g. `Dense`, `Convolution`, `Pool`.

            - output_shape : list
                The output dimensions of the layer.

            In addition, `Dense` and `Convolution` layer types contain

            - weights : array
                The weight parameters connecting the layer with the next.

            `Convolution` layers contain further

            - nb_col : int
                The x-dimension of filters.

            - nb_row : int
                The y-dimension of filters.

            - border_mode : string
                How to handle borders during convolution, e.g. `full`, `valid`,
                `same`.

            `Pooling` layers contain

            - pool_size : list
                Specifies the subsampling factor in each dimension.

            - strides : list
                The stepsize in each dimension during pooling.

    Returns
    -------

        ann : dict
            Dictionary containing the parsed network.

    """

    input_shape = model.input_shape

    layers = []
    labels = []
    layer_idx_map = []
    for (layer_num, layer) in enumerate(model.layers):
        attributes = {'layer_num': layer_num,
                      'layer_type': layer.__class__.__name__,
                      'output_shape': layer.output_shape}

        # Append layer label
        if len(attributes['output_shape']) == 2:
            shape_string = '_{}'.format(attributes['output_shape'][1])
        else:
            shape_string = '_{}x{}x{}'.format(attributes['output_shape'][1],
                                              attributes['output_shape'][2],
                                              attributes['output_shape'][3])
        num_str = str(layer_num) if layer_num > 9 else '0' + str(layer_num)
        labels.append(num_str + attributes['layer_type'] + shape_string)
        attributes.update({'label': labels[-1]})

        next_layer = model.layers[layer_num + 1] \
            if layer_num + 1 < len(model.layers) else None
        next_layer_name = next_layer.__class__.__name__ if next_layer else None
        if next_layer_name == 'BatchNormalization' and \
                attributes['layer_type'] not in bn_layers:
            raise NotImplementedError(
                "A batchnormalization layer must follow a layer of type " +
                "{}, not {}.".format(bn_layers, attributes['layer_type']))

        if attributes['layer_type'] in {'Dense', 'Convolution2D'}:
            wb = layer.get_weights()
            if next_layer_name == 'BatchNormalization':
                weights = next_layer.get_weights()
                # W, b, gamma, beta, mean, std, epsilon
                wb = absorb_bn(wb[0], wb[1], weights[0], weights[1],
                               weights[2], weights[3], next_layer.epsilon)
            attributes.update({'weights': wb})

        if attributes['layer_type'] == 'Convolution2D':
            attributes.update({'input_shape': layer.input_shape,
                               'nb_filter': layer.nb_filter,
                               'nb_col': layer.nb_col,
                               'nb_row': layer.nb_row,
                               'border_mode': layer.border_mode})

        elif attributes['layer_type'] in {'MaxPooling2D', 'AveragePooling2D'}:
            attributes.update({'input_shape': layer.input_shape,
                               'pool_size': layer.pool_size,
                               'strides': layer.strides,
                               'border_mode': layer.border_mode})

        if attributes['layer_type'] in {'Activation', 'AveragePooling2D',
                                        'MaxPooling2D'}:
            attributes.update({'get_activ': get_activ_fn_for_layer(model,
                                                                   layer_num)})
        layers.append(attributes)
        layer_idx_map.append(layer_num)

    return {'input_shape': input_shape, 'layers': layers, 'labels': labels,
            'layer_idx_map': layer_idx_map}


def get_activ_fn_for_layer(model, i):
    return theano.function(
        [model.layers[0].input, theano.In(K.learning_phase(), value=0)],
        model.layers[i].output, allow_input_downcast=True,
        on_unused_input='ignore')


def model_from_py(filename):
    mod = import_script(filename)
    return {'model': mod.build_network()}


def load_ann(filename, path=None):
    """
    Load network from file.

    Parameters
    ----------

    model : dict
        A dictionary of objects that constitute the input model. It must
        contain the following two keys:

        - 'model': Model instance of the network in the respective
          ``model_lib``.
        - 'val_fn': Theano function that allows evaluating the original
          model.

        For instance, if the input model was written using Keras, the
        'model'-value would be an instance of ``keras.Model``, and
        'val_fn' the ``keras.Model.evaluate`` method.

    """

    if path is None:
        path = settings['path']
    if settings['dataset'] == 'caltech101':
        model = model_from_py(filename)['model']
    else:
        from keras import models
        model = models.model_from_json(open(
            os.path.join(path, filename + '.json')).read())
    model.load_weights(os.path.join(path, filename + '.h5'))
    # Todo: Allow user to specify loss function here (optimizer is not
    # relevant as we do not train any more). Unfortunately, Keras does not
    # save these parameters. They can be obtained from the compiled model
    # by calling 'model.loss' and 'model.optimizer'.
    model.compile(loss='categorical_crossentropy', optimizer='sgd',
                  metrics=['accuracy'])
    return {'model': model, 'val_fn': model.evaluate}


def evaluate(val_fn, X_test, Y_test):
    """Evaluate the original ANN."""
    return val_fn(X_test, Y_test)


def set_layer_params(model, params, i):
    """Set ``params`` of layer ``i`` of a given ``model``."""
    model.layers[i].set_weights(params)
