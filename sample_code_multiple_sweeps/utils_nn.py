import numpy as np
import matplotlib.pyplot as plt
import tensorflow as tf
import tensorflow.keras.backend as K
from tensorflow.keras.models import Model, load_model
from tensorflow.keras.layers import Dense, Input, Concatenate, Lambda
from scipy.stats import entropy

import config as cf
from targets import target_distribution_gen

def build_model():
    cf.pnn.inputsize = 3 # Number of hidden variables, e.g. alpha, beta, gamma
    """ Build NN for triangle """
    # Hidden variables as inputs.
    inputTensor = Input((cf.pnn.inputsize,))

    # Group input tensor according to whether alpha, beta or gamma hidden variable.
    group_alpha = Lambda(lambda x: x[:,:1], output_shape=((1,)))(inputTensor)
    group_beta = Lambda(lambda x: x[:,1:2], output_shape=((1,)))(inputTensor)
    group_gamma = Lambda(lambda x: x[:,2:3], output_shape=((1,)))(inputTensor)

    # Neural network at the sources, for pre-processing (e.g. for going from uniform distribution to non-uniform one)
    ## Note that in the example code greek_depth is set to 0, so this part is trivial.
    for _ in range(cf.pnn.greek_depth):
        group_alpha = Dense(cf.pnn.greek_width,activation=cf.pnn.activ, kernel_regularizer=cf.pnn.kernel_reg)(group_alpha)
        group_beta = Dense(cf.pnn.greek_width,activation=cf.pnn.activ, kernel_regularizer=cf.pnn.kernel_reg)(group_beta)
        group_gamma = Dense(cf.pnn.greek_width,activation=cf.pnn.activ, kernel_regularizer=cf.pnn.kernel_reg)(group_gamma)

    # Route hidden variables to visibile parties Alice, Bob and Charlie
    group_a = Concatenate()([group_beta,group_gamma])
    group_b = Concatenate()([group_gamma,group_alpha])
    group_c = Concatenate()([group_alpha,group_beta])

    # Neural network at the parties Alice, Bob and Charlie.
    ## Note: increasing the variance of the initialization seemed to help in some cases, especially when the number if outputs per party is 4 or more.
    kernel_init = tf.keras.initializers.VarianceScaling(scale=cf.pnn.weight_init_scaling, mode='fan_in', distribution='truncated_normal', seed=None)
    for _ in range(cf.pnn.latin_depth):
        group_a = Dense(cf.pnn.latin_width,activation=cf.pnn.activ, kernel_regularizer=cf.pnn.kernel_reg, kernel_initializer = kernel_init)(group_a)
        group_b = Dense(cf.pnn.latin_width,activation=cf.pnn.activ, kernel_regularizer=cf.pnn.kernel_reg, kernel_initializer = kernel_init)(group_b)
        group_c = Dense(cf.pnn.latin_width,activation=cf.pnn.activ, kernel_regularizer=cf.pnn.kernel_reg, kernel_initializer = kernel_init)(group_c)

    # Apply final softmax layer
    group_a = Dense(cf.pnn.a_outputsize,activation=cf.pnn.activ2, kernel_regularizer=cf.pnn.kernel_reg)(group_a)
    group_b = Dense(cf.pnn.b_outputsize,activation=cf.pnn.activ2, kernel_regularizer=cf.pnn.kernel_reg)(group_b)
    group_c = Dense(cf.pnn.c_outputsize,activation=cf.pnn.activ2, kernel_regularizer=cf.pnn.kernel_reg)(group_c)

    outputTensor = Concatenate()([group_a,group_b,group_c])

    model = Model(inputTensor,outputTensor)
    return model

def np_euclidean_distance(p,q=0):
    """ Euclidean distance, useful for plotting results."""
    return np.sqrt(np.sum(np.square(p-q),axis=-1))

def np_distance(p,q=0):
    """ Same as the distance used in the loss function, just written for numpy arrays."""
    #return np.sum(np.square(p-q),axis=-1)
    #return 0.5*np.sum(np.abs(p-q),axis=-1)
    p = np.clip(p, K.epsilon(), 1)
    q = np.clip(q, K.epsilon(), 1)
    return np.sum(p * np.log(np.divide(p,q)), axis=-1)

def keras_distance(p,q):
    """ Distance used in loss function. """
    #return K.sum(K.square(p-q),axis=-1)
    #return 0.5*K.sum(K.abs(p-q), axis=-1)
    p = K.clip(p, K.epsilon(), 1)
    q = K.clip(q, K.epsilon(), 1)
    return K.sum(p * K.log(p / q), axis=-1)

def customLoss_distr(y_pred):
    """ Converts the output of the neural network to a probability vector.
    That is from a shape of (batch_size, a_outputsize + b_outputsize + c_outputsize) to a shape of (a_outputsize * b_outputsize * c_outputsize,)
    """
    a_probs = y_pred[:,0:cf.pnn.a_outputsize]
    b_probs = y_pred[:,cf.pnn.a_outputsize : cf.pnn.a_outputsize + cf.pnn.b_outputsize]
    c_probs = y_pred[:,cf.pnn.a_outputsize + cf.pnn.b_outputsize : cf.pnn.a_outputsize + cf.pnn.b_outputsize + cf.pnn.c_outputsize]

    a_probs = K.reshape(a_probs,(-1,cf.pnn.a_outputsize,1,1))
    b_probs = K.reshape(b_probs,(-1,1,cf.pnn.b_outputsize,1))
    c_probs = K.reshape(c_probs,(-1,1,1,cf.pnn.c_outputsize))

    probs = a_probs*b_probs*c_probs
    probs = K.mean(probs,axis=0)
    probs = K.flatten(probs)
    return probs

def customLoss(y_true,y_pred):
    """ Custom loss function."""
    # Note that y_true is just batch_size copies of the target distributions. So any row could be taken here. We just take 0-th row.
    return keras_distance(y_true[0,:], customLoss_distr(y_pred))

# Set up generator for X and Y data
training_mean = 0.5
training_sigma = 0.28867513459 #= np.sqrt(1/12)

def generate_xy_batch():
    while True:
        temp = np.divide((np.random.random((cf.pnn.batch_size, cf.pnn.inputsize)) - training_mean),training_sigma)
        yield (temp, cf.pnn.y_true)

def generate_x_test():
    while True:
        temp = np.divide((np.random.random((cf.pnn.batch_size_test, cf.pnn.inputsize)) - training_mean),training_sigma)
        yield temp

def single_evaluation(model):
    """ Evaluates the model and returns the resulting distribution as a numpy array. """
    test_pred = model.predict_generator(generate_x_test(), steps=1, max_queue_size=10, workers=1, use_multiprocessing=False, verbose=0)
    result = K.eval(customLoss_distr(test_pred))
    return result

def single_run():
    """ Runs training algorithm for a single target distribution. Returns model."""
    # Model and optimizer related setup.
    K.clear_session()
    model = build_model()
    if cf.pnn.start_from is not None:
        print("LOADING MODEL WEIGHTS FROM", cf.pnn.start_from)
        model = load_model(cf.pnn.start_from,custom_objects={'customLoss': customLoss})

    if cf.pnn.optimizer.lower() == 'adadelta':
        optimizer = tf.keras.optimizers.Adadelta(lr=cf.pnn.lr, rho=0.95, epsilon=None, decay=cf.pnn.decay)
    elif cf.pnn.optimizer.lower() == 'sgd':
        optimizer = tf.keras.optimizers.SGD(lr=cf.pnn.lr, decay=cf.pnn.decay, momentum=cf.pnn.momentum, nesterov=True)
    else:
        optimizer = tf.keras.optimizers.SGD(lr=cf.pnn.lr, decay=cf.pnn.decay, momentum=cf.pnn.momentum, nesterov=True)
        print("\n\nWARNING!!! Optimizer {} not recognized. Please implement it if you want to use it. Using SGD instead.\n\n".format(cf.pnn.optimizer))
        cf.pnn.optimizer = 'sgd' # set it for consistency.

    model.compile(loss=customLoss, optimizer = optimizer, metrics=[])

    # Fit model
    model.fit_generator(generate_xy_batch(), steps_per_epoch=cf.pnn.no_of_batches, epochs=1, verbose=1, validation_data=generate_xy_batch(), validation_steps=cf.pnn.no_of_validation_batches, class_weight=None, max_queue_size=10, workers=1, use_multiprocessing=False, shuffle=False, initial_epoch=0)
    return model