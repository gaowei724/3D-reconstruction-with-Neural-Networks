import os
import numpy as np
import tensorflow as tf
import matplotlib.pyplot as plt
import lib.recurrent_module as recurrent_module
import lib.utils as utils
from datetime import datetime

# Recurrent Reconstruction Neural Network (R2N2)


class R2N2:
    def __init__(self, params=None):
        self.session_loss = []
        self.create_time = datetime.now().strftime("%Y-%m-%d_%H:%M:%S")

        # read params
        if params is None:
            self.learn_rate, self.batch_size, self.epoch_count = utils.get_params_from_disk()
        else:
            self.learn_rate = params['learn_rate']
            self.batch_size = params['batch_size']
            self.epoch_count = params['epoch_count']

        print("learn_rate {}, epoch_count {}, batch_size {}".format(
            self.learn_rate, self.epoch_count, self.batch_size))

        # place holders
        print("creating network...")
        self.X = tf.placeholder(tf.float32, [None, 24, 137, 137, 4])
        self.Y = tf.placeholder(tf.uint8, [None, 32, 32, 32])
        cur_tensor = self.X

        print("encoder_network")
        with tf.name_scope("encoder_network"):
            k_s = [3, 3]
            conv_filter_count = [96, 128, 256, 256, 256, 256]

            for i in range(7):
                if i < 6:
                    k_s = [7, 7] if i is 0 else k_s
                    cur_tensor = tf.map_fn(lambda a: tf.layers.conv2d(
                        a, filters=conv_filter_count[i], padding='SAME', kernel_size=k_s, activation=None),  cur_tensor, parallel_iterations=5)
                    cur_tensor = tf.map_fn(
                        lambda a: tf.layers.max_pooling2d(a, 2, 2),  cur_tensor, parallel_iterations=5)
                    cur_tensor = tf.map_fn(
                        tf.nn.relu,  cur_tensor, parallel_iterations=5)
                elif i == 6:
                    cur_tensor = tf.map_fn(
                        tf.contrib.layers.flatten,  cur_tensor)
                    cur_tensor = tf.map_fn(lambda a: tf.contrib.layers.fully_connected(
                        a, 1024, activation_fn=None), cur_tensor)
                    cur_tensor = tf.map_fn(tf.nn.relu,  cur_tensor)
                # print(cur_tensor.shape)

        cur_tensor = tf.verify_tensor_all_finite(
            cur_tensor, "fc vector (encoder output)")

        print("recurrent_module")
        with tf.name_scope("recurrent_module"):
            rnn = recurrent_module.GRU_GRID()
            hidden_state = None
            for t in range(24):  # feed batches of seqeuences
                hidden_state = tf.verify_tensor_all_finite(rnn.call(
                    cur_tensor[:, t, :], hidden_state), "hidden_state {}".format(t))
        cur_tensor = hidden_state
        # print(cur_tensor.shape)

        print("decoder_network")
        with tf.name_scope("decoder_network"):
            k_s = [3, 3, 3]
            deconv_filter_count = [128, 128, 128, 64, 32, 2]

            for i in range(6):
                if i == 0:
                    cur_tensor = utils.r2n2_unpool3D(cur_tensor)
                elif i in range(1, 3):  # scale up hidden state to 32*32*32
                    cur_tensor = tf.layers.conv3d(
                        cur_tensor, padding='SAME', filters=deconv_filter_count[i], kernel_size=k_s, activation=None)
                    cur_tensor = tf.nn.relu(cur_tensor)
                    cur_tensor = utils.r2n2_unpool3D(cur_tensor)
                elif i in range(3, 5):  # reduce number of channels to 2
                    cur_tensor = tf.layers.conv3d(
                        cur_tensor, padding='SAME', filters=deconv_filter_count[i], kernel_size=k_s, activation=None)
                    cur_tensor = tf.nn.relu(cur_tensor)
                elif i == 5:  # final conv before softmax
                    cur_tensor = tf.layers.conv3d(
                        cur_tensor, padding='SAME', filters=deconv_filter_count[i], kernel_size=k_s, activation=None)
                # print(cur_tensor.shape)

        print("loss_function")
        logits = tf.verify_tensor_all_finite(
            cur_tensor, "logits (decoder output)")
        softmax = tf.nn.softmax(logits)
        log_softmax = tf.nn.log_softmax(logits)  # avoids log(0)
        label = tf.one_hot(self.Y, 2)
        cross_entropy = tf.reduce_sum(-tf.multiply(label,
                                                   log_softmax), axis=-1)
        losses = tf.reduce_mean(cross_entropy, axis=[1, 2, 3])
        batch_loss = tf.reduce_mean(losses)
        self.loss = batch_loss

        # misc
        step_count = tf.Variable(0, trainable=False)
        lr = self.learn_rate
        optimizer = tf.train.GradientDescentOptimizer(
            learning_rate=lr)
        grads_and_vars = optimizer.compute_gradients(batch_loss)
        map(lambda a: tf.verify_tensor_all_finite(
            a[0], "grads_and_vars"), grads_and_vars)  # assert no Nan or Infs in grad

        self.final_op = optimizer.apply_gradients(
            grads_and_vars, global_step=step_count)
        self.print = tf.Print(batch_loss, [batch_loss, lr])

        print("...network created")
        self.saver = tf.train.Saver()
        self.sess = tf.InteractiveSession()
        self.prediction = tf.argmax(softmax, -1)
        tf.global_variables_initializer().run()

    def train_step(self, data, label):
        x = utils.to_npy(data)
        y = utils.to_npy(label)
        return self.sess.run([self.final_op, self.print, self.loss], {self.X: x, self.Y: y})[2]

    def save(self, save_dir):
        if not os.path.isdir(save_dir):
            os.makedirs(save_dir)
        self.saver.save(self.sess, "{}/model.ckpt".format(save_dir))
        np.save("{}/loss.npy".format(save_dir), self.session_loss)
        self.plot_loss(save_dir, self.session_loss)

    def restore(self, model_dir):
        self.saver = tf.train.import_meta_graph(
            "{}/model.ckpt.meta".format(model_dir))
        self.saver.restore(self.sess, tf.train.latest_checkpoint(model_dir))

    def predict(self, x):
        return self.sess.run([self.prediction], {self.X: x})[0]

    def plot_loss(self, plot_dir, loss_arr):
        if not os.path.isdir(plot_dir):
            os.makedirs(plot_dir)
        plt.plot(np.array(loss_arr).flatten())
        plt.savefig("{}/loss.png".format(plot_dir), bbox_inches='tight')
        plt.close()

    def vis(self, log_dir="./log"):
        writer = tf.summary.FileWriter(log_dir)
        writer.add_graph(self.sess.graph)
