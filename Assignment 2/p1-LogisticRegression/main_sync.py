import tensorflow as tf
import input_data

# Disable eager execution for tensorflow 2.0
tf.compat.v1.disable_eager_execution()

# Initialize epoch and batch size
epoch = 50
batch_size = 50

# Define the command line flags that can be sent
tf.compat.v1.flags.DEFINE_integer("task_index", 0, "Index of task.")
tf.compat.v1.flags.DEFINE_string("job_name", "worker", "worker or ps")
tf.compat.v1.flags.DEFINE_string("deploy_mode", "single", "single or cluster")
FLAGS = tf.compat.v1.flags.FLAGS

# Define cluster specs
clusterSpec_single = tf.train.ClusterSpec({
    "worker": [
        "localhost:2222"
    ]
})

clusterSpec_cluster = tf.train.ClusterSpec({
    "ps": [
        "node0:2222"
    ],
    "worker": [
        "node0:2223",
        "node1:2222"
    ]
})

clusterSpec_cluster2 = tf.train.ClusterSpec({
    "ps": [
        "node0:2222"
    ],
    "worker": [
        "node0:2223",
        "node1:2222",
        "node2:2222",
    ]
})

clusterSpec = {
    "single": clusterSpec_single,
    "cluster": clusterSpec_cluster,
    "cluster2": clusterSpec_cluster2
}

clusterinfo = clusterSpec[FLAGS.deploy_mode]
num_workers = clusterinfo.num_tasks("worker")

# Define distributed server
server = tf.distribute.Server(
    clusterinfo,
    job_name=FLAGS.job_name,
    task_index=FLAGS.task_index
)


if FLAGS.job_name == "ps":
    server.join()
elif FLAGS.job_name == "worker":

    mnist = input_data.read_data_sets("MNIST_data/", one_hot=True)

    # Setup devices
    with tf.device(
        tf.train.replica_device_setter(
            worker_device="/job:worker/task:%d" % FLAGS.task_index,
            cluster=clusterinfo
        )
    ):

        # Initialize placeholder for inputs and variable for parameters
        x = tf.compat.v1.placeholder(tf.float32, [None, 784])
        y = tf.compat.v1.placeholder(tf.float32, [None, 10])
        W = tf.Variable(tf.random.uniform([784, 10]))
        b = tf.Variable(tf.random.uniform([10]))
        global_step = tf.Variable(0, dtype=tf.int32, trainable=False)

        # Define a function for predictied digit
        pred = tf.nn.softmax(tf.matmul(x, W) + b)

        # Define loss function
        loss = tf.reduce_mean(
            input_tensor=-tf.reduce_sum(
                input_tensor=y * tf.math.log(pred),
                axis=1
            )
        )

        # Define synchronous optimizer
        optimizer = tf.compat.v1.train.SyncReplicasOptimizer(
            tf.compat.v1.train.GradientDescentOptimizer(0.01),
            replicas_to_aggregate=num_workers,
            total_num_replicas=num_workers
        )

        hook = optimizer.make_session_run_hook(
            FLAGS.task_index == 0,
            num_tokens=0
        )

        optimizer = optimizer.minimize(loss, global_step=global_step)

        # Define variables used for testing
        pred_label = tf.argmax(input=pred, axis=1)
        actual_label = tf.argmax(input=y, axis=1)
        accuracy = tf.reduce_mean(
            input_tensor=tf.cast(
                tf.equal(pred_label, actual_label),
                tf.float32
            )
        )

        # Define session configuration
        sess_config = tf.ConfigProto(
            allow_soft_placement=True,
            log_device_placement=False,
            device_filters=[
                "/job:ps",
                "/job:worker/task:%d" % FLAGS.task_index
            ]
        )

        # Summary for accuracy and loss
        tf.compat.v1.summary.scalar("Accuracy", accuracy)
        tf.compat.v1.summary.scalar("Loss", loss)
        metric = tf.compat.v1.summary.merge_all()

    # Initialize globlvariables
    init = tf.compat.v1.global_variables_initializer()

    # Start the training session
    with tf.compat.v1.train.MonitoredTrainingSession(
        master="grpc://" + clusterinfo.task_address("worker", 0),
        is_chief=FLAGS.task_index == 0,
        hooks=[hook],
        config=sess_config,
        stop_grace_period_secs=10,
    ) as sess:
        sess.run(init)

        # Present summary in TensorBoard
        writer = tf.compat.v1.summary.FileWriter("summary", sess.graph)

        # Iterate each epoh
        for i in range(epoch):
            num_batch = mnist.train.num_examples // batch_size

            # Iterate each batch
            for j in range(num_batch):
                x_train, y_train = mnist.train.next_batch(batch_size)
                sess.run(optimizer, feed_dict={x: x_train, y: y_train})

            # Evaluate the model in each epoch
            x_test, y_test = mnist.test.next_batch(batch_size)
            a, l, summary, _ = sess.run(
                [accuracy, loss, metric, global_step],
                feed_dict={x: x_test, y: y_test}
            )
            print("Epoch: %d, Loss: %f, Accuracy: %f" % (i, l, a))

            # Record accuracy and loss to the summary
            writer.add_summary(summary, i)

        writer.close()
